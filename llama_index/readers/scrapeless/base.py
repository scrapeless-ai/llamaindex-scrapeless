"""SDK-backed readers with explicit completion checks and bounded polling."""

from __future__ import annotations

import asyncio
import inspect
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from llama_index.core.readers.base import BaseReader
from llama_index.core.schema import Document

from .errors import (
    ScrapelessCrawlPageError,
    ScrapelessReaderError,
    ScrapelessTaskError,
    ScrapelessTaskTimeout,
)
from .mapping import Mapper, Selector, check_failure, documents, plain

SERVICES = (
    "scrape",
    "batch_scrape",
    "crawl",
    "google_search",
    "scraping",
    "web_unlocker",
    "ai_scraper",
)
PENDING = {"pending", "running", "active", "scraping", "queued", "waiting", "paused"}


def _omit_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _omit_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_omit_none(item) for item in value]
    return value


def _task_identity(current: str | None, returned: Any) -> str | None:
    if returned is None:
        return current
    if not isinstance(returned, str) or not returned.strip():
        raise ScrapelessReaderError("The task identifier must be a nonempty string.")
    if current is not None and current != returned:
        raise ScrapelessReaderError(
            "The response task identifier does not match the requested task."
        )
    return returned


def _task_acknowledgement(value: Any) -> bool:
    status = value.get("status") if isinstance(value, Mapping) else None
    return (
        isinstance(value, Mapping)
        and (
            bool({"taskId", "task_id"}.intersection(value))
            or (isinstance(status, str) and status in PENDING)
        )
        and set(value).issubset({"taskId", "task_id", "status", "message", "success", "code"})
    )


def _check_result_error(value: Any) -> None:
    # A service may return structured business data with its own status/success
    # fields. Only recognize a narrow, content-free API error envelope here.
    if isinstance(value, Mapping) and set(value).issubset(
        {
            "success",
            "status",
            "error",
            "message",
            "msg",
            "code",
            "traceId",
            "taskId",
            "task_id",
        }
    ):
        check_failure(value)


def _validate_url(value: Any) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("Crawl URLs must be nonempty HTTP(S) URL strings.")
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"https", "http"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Crawl URLs must have an HTTP(S) scheme and a hostname.")


def _failed_crawl_page(page: Any) -> bool:
    try:
        check_failure(page)
    except ScrapelessReaderError:
        return True
    meta = page.get("metadata", {}) if isinstance(page, Mapping) else {}
    if not isinstance(meta, Mapping):
        return False
    code = meta.get("statusCode")
    if isinstance(code, str) and len(code) == 3 and code.isascii() and code.isdecimal():
        code = int(code)
    return bool(meta.get("error")) or (isinstance(code, int) and code >= 400)


class ScrapelessReader(BaseReader):
    """Load completed data from the documented Scrapeless data services.

    Args:
        api_key: Optional key; the SDK otherwise reads SCRAPELESS_API_KEY.
        client: Optional configured Scrapeless SDK client, also useful for testing.
        timeout: Polling budget in seconds. An in-flight SDK request can overrun it.
        poll_interval: Seconds between status checks; must be positive.
        request_timeout: SDK per-request timeout in seconds.
        sdk_config: Additional SDK config, such as documented endpoint overrides.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any = None,
        timeout: float = 120,
        poll_interval: float = 2,
        request_timeout: float = 30,
        sdk_config: dict[str, Any] | None = None,
    ) -> None:
        for name, value in (
            ("timeout", timeout),
            ("poll_interval", poll_interval),
            ("request_timeout", request_timeout),
        ):
            try:
                valid = not isinstance(value, bool) and math.isfinite(value) and value > 0
            except (TypeError, ValueError, OverflowError):
                valid = False
            if not valid:
                raise ValueError(f"{name} must be a finite positive number of seconds.")
        if client is not None and (api_key is not None or sdk_config is not None):
            raise ValueError("Supply client or api_key/sdk_config, not both.")
        if client is None:
            from scrapeless import Scrapeless

            config = dict(sdk_config or {})
            config["timeout"] = request_timeout * 1000
            if api_key is not None:
                config["api_key"] = api_key
            client = Scrapeless(config)
        self.client = client
        self.timeout = timeout
        self.poll_interval = poll_interval

    def _pause(self, deadline: float, service: str, task_id: str) -> None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ScrapelessTaskTimeout(service, task_id)
        time.sleep(min(self.poll_interval, remaining))
        self._check_deadline(deadline, service, task_id)

    @staticmethod
    def _check_deadline(deadline: float, service: str, task_id: str) -> None:
        if time.monotonic() >= deadline:
            raise ScrapelessTaskTimeout(service, task_id)

    @staticmethod
    def _poll_result(service: str, task_id: str, fetch: Callable, *args: Any) -> Any:
        try:
            return fetch(*args)
        except Exception as error:
            raise ScrapelessTaskError(service, task_id) from error

    def load_data(
        self,
        service: str = "scrape",
        *,
        params: dict[str, Any] | None = None,
        task_id: str | None = None,
        mapping_function: Mapper | None = None,
        records_selector: Selector | None = None,
    ) -> list[Document]:
        """Run a named data service and return Documents, or resume its task.

        ``params`` is service-specific; see the README coverage table. For a
        resumed task, pass the same original params to preserve request context.
        Selectors see the completed payload, after the task envelope is removed.
        """
        if service not in SERVICES:
            raise ValueError(f"Unknown service {service!r}. Choose one of {SERVICES}.")
        if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
            raise ValueError("task_id must be a nonempty string.")
        if params is not None and not isinstance(params, Mapping):
            raise ValueError("params must be a dictionary or None.")
        params = dict(params) if params is not None else {}
        if task_id is None and service in {
            "google_search",
            "scraping",
            "ai_scraper",
            "web_unlocker",
        }:
            actor = params.get(
                "actor", "scraper.google.search" if service == "google_search" else None
            )
            if not isinstance(actor, str) or not actor.strip():
                raise ValueError("actor must be a nonempty string.")
            if not isinstance(params.get("input"), Mapping):
                raise ValueError("input must be a dictionary.")
        for name, function in (
            ("mapping_function", mapping_function),
            ("records_selector", records_selector),
        ):
            if function is not None and (
                not callable(function) or inspect.iscoroutinefunction(function)
            ):
                raise ValueError(f"{name} must be a synchronous callable or None.")
        deadline = time.monotonic() + self.timeout
        context: dict[str, Any] = {"service": service}
        if params.get("actor"):
            context["actor"] = params["actor"]
        elif service == "google_search":
            context["actor"] = "scraper.google.search"
        request_input = params.get("input", {})
        if isinstance(request_input, Mapping):
            for key in ("q", "prompt", "country", "gl", "hl"):
                if key in request_input:
                    context[key] = request_input[key]
            if service == "web_unlocker" and request_input.get("url"):
                context["url"] = request_input["url"]
                context["requested_url"] = request_input["url"]
        if service == "scrape" and params.get("url"):
            context["url"] = params["url"]
        try:
            if service in {"scrape", "batch_scrape", "crawl"}:
                payload, task_id = self._crawl(service, params, task_id, deadline)
            elif service in {"google_search", "scraping"}:
                payload, task_id = self._scraping(service, params, task_id, deadline)
                if service == "google_search" and records_selector is None:
                    if not isinstance(payload, Mapping) or not isinstance(
                        payload.get("organic_results"), list
                    ):
                        raise ScrapelessReaderError(
                            "Expected organic_results; use records_selector for another "
                            "search schema."
                        )
                    payload = payload["organic_results"]
                    context["content_kind"] = "search_result"
            elif service == "ai_scraper":
                payload, task_id = self._ai(params, task_id, deadline)
            else:
                if task_id is not None:
                    raise ValueError("web_unlocker does not support task_id resumption.")
                from scrapeless.types import UniversalScrapingRequest

                payload = self.client.universal.scrape(UniversalScrapingRequest(**params))
                _check_result_error(payload)
                if _task_acknowledgement(payload):
                    raise ScrapelessReaderError(
                        "Web Unlocker returned a task acknowledgement, not completed content."
                    )
            if task_id:
                context["task_id"] = task_id
            try:
                return documents(
                    payload, mapper=mapping_function, selector=records_selector, metadata=context
                )
            except (TypeError, ValueError) as error:
                if mapping_function is not None or records_selector is not None:
                    raise
                raise ScrapelessReaderError("The service returned a malformed record.") from error
        except ScrapelessReaderError as error:
            error.service = service
            if error.task_id is None:
                error.task_id = task_id
            raise

    async def aload_data(self, *args: Any, **kwargs: Any) -> list[Document]:
        """Run the synchronous SDK in a worker thread, not on the event loop.

        Cancelling this await does not stop its worker or the remote task. Use a
        separate client for concurrent calls when the SDK/client is mutable.
        """
        return await asyncio.to_thread(self.load_data, *args, **kwargs)

    def _crawl(
        self, service: str, params: dict[str, Any], task_id: str | None, deadline: float
    ) -> tuple[Any, str]:
        from scrapeless.types import CrawlParams, CrawlScrapeOptions, ScrapeParams

        try:
            raw_options = params.get("options")
            if raw_options is not None and not isinstance(raw_options, Mapping):
                raise ValueError("Crawl options must be a dictionary or None.")
            options = dict(raw_options) if raw_options is not None else {}
            allowed = {"urls", "options"} if service == "batch_scrape" else {"url", "options"}
            if set(params) - allowed:
                raise ValueError(
                    "Crawl params accept url/urls and options; put API options in options."
                )
            root = self.client.scraping_crawl
            endpoint_kind = {"scrape": "scrape", "batch_scrape": "scrape/batch", "crawl": "crawl"}[
                service
            ]
            sdk_service = root.crawl if service == "crawl" else root.scrape
            if task_id is None:
                if service == "batch_scrape":
                    urls = params.get("urls")
                    if not isinstance(urls, list) or not urls:
                        raise ValueError("urls must be a nonempty list.")
                    for url in urls:
                        _validate_url(url)
                else:
                    _validate_url(params.get("url"))
                if service == "crawl":
                    if isinstance(options.get("scrapeOptions"), dict):
                        options["scrapeOptions"] = CrawlScrapeOptions(**options["scrapeOptions"])
                    body = {"url": params["url"], **asdict(CrawlParams(**options))}
                elif service == "batch_scrape":
                    urls = params["urls"]
                    body = {
                        "urls": urls,
                        "ignoreInvalidURLs": False,
                        **asdict(ScrapeParams(**options)),
                    }
                else:
                    body = {"url": params["url"], **asdict(ScrapeParams(**options))}
                # SDK 1.3.0 convenience methods serialize unset dataclass fields as
                # null. The API rejects those option values; use the same SDK
                # transport with omitted unset fields, including nested options.
                created = sdk_service.request(
                    f"/api/v1/crawler/{endpoint_kind}", "POST", _omit_none(body)
                )
                created = plain(created)
                task_id = _task_identity(
                    None, created.get("id") if isinstance(created, Mapping) else None
                )
                check_failure(created)
                if task_id is None:
                    raise ScrapelessReaderError("Crawl task creation did not return an id.")
            # Use the SDK request layer to retain pagination fields. The SDK's
            # check_crawl_status converts to a dataclass and can hide the next cursor.
            endpoint = f"/api/v1/crawler/{endpoint_kind}/{quote(task_id, safe='')}"
            while True:
                self._check_deadline(deadline, service, task_id)
                result = self._poll_result(service, task_id, sdk_service.request, endpoint, "GET")
                check_failure(result)
                status = result.get("status") if isinstance(result, Mapping) else None
                if not isinstance(status, str):
                    raise ScrapelessReaderError(
                        "Crawl returned a missing or malformed task status."
                    )
                if status == "completed":
                    if "data" not in result or result["data"] is None:
                        raise ScrapelessReaderError("Completed Crawl task is missing data.")
                    payload = result["data"]
                    if service != "scrape":
                        if not isinstance(payload, list):
                            raise ScrapelessReaderError("Expected a list of Crawl result pages.")
                        payload = self._crawl_pages(
                            sdk_service, result, endpoint, service, task_id, deadline
                        )
                    pages = [
                        plain(page)
                        for page in (payload if isinstance(payload, list) else [payload])
                    ]
                    failed = [index for index, page in enumerate(pages) if _failed_crawl_page(page)]
                    if failed:
                        raise ScrapelessCrawlPageError(pages, failed)
                    return payload, task_id
                if status not in PENDING:
                    raise ScrapelessReaderError("Crawl returned an unknown or unsuccessful status.")
                self._pause(deadline, service, task_id)
        except ScrapelessReaderError as error:
            if task_id is not None:
                error.task_id = task_id
            raise

    def _crawl_pages(
        self,
        sdk_service: Any,
        result: Mapping,
        endpoint: str,
        service: str,
        task_id: str,
        deadline: float,
    ) -> list[Any]:
        payload = list(result["data"])
        seen = {endpoint}
        while (next_url := result.get("next")) is not None and next_url != "":
            # SDK request concatenates base_url + endpoint. Convert an absolute
            # same-origin cursor to a relative path before calling the SDK.
            if not isinstance(next_url, str) or any(
                ord(char) <= 32 or ord(char) == 127 for char in next_url
            ):
                raise ScrapelessReaderError("Unsupported Crawl pagination URL.")
            try:
                parsed = urlsplit(next_url)
                if parsed.netloc:
                    base = urlsplit(sdk_service.base_url)
                    if (parsed.scheme, parsed.netloc) != (base.scheme, base.netloc):
                        raise ScrapelessReaderError(
                            "Crawl pagination must stay on the SDK API origin."
                        )
            except ValueError as error:
                raise ScrapelessReaderError("Unsupported Crawl pagination URL.") from error
            if parsed.path != endpoint or parsed.fragment or (parsed.scheme and not parsed.netloc):
                raise ScrapelessReaderError("Unsupported Crawl pagination URL.")
            next_url = urlunsplit(("", "", parsed.path, parsed.query, ""))
            if next_url in seen:
                raise ScrapelessReaderError("Crawl returned a repeated pagination cursor.")
            seen.add(next_url)
            self._check_deadline(deadline, service, task_id)
            result = self._poll_result(service, task_id, sdk_service.request, next_url, "GET")
            check_failure(result)
            if not isinstance(result, Mapping) or not isinstance(result.get("data"), list):
                raise ScrapelessReaderError("Crawl pagination is missing its data list.")
            if result.get("status", "completed") != "completed":
                raise ScrapelessReaderError("Crawl pagination returned an unfinished page.")
            payload.extend(result["data"])
        return payload

    def _scraping(
        self, service: str, params: dict[str, Any], task_id: str | None, deadline: float
    ) -> tuple[Any, str | None]:
        from scrapeless.types import ScrapingTaskRequest

        try:
            sdk_service = (
                self.client.deepserp if service == "google_search" else self.client.scraping
            )
            if task_id is None:
                params = dict(params)
                if service == "google_search":
                    params.setdefault("actor", "scraper.google.search")
                response = sdk_service.create_task(ScrapingTaskRequest(**params))
            else:
                self._check_deadline(deadline, service, task_id)
                response = self._poll_result(
                    service, task_id, sdk_service.get_task_result, quote(task_id, safe="")
                )
            while True:
                if not isinstance(response, Mapping) or "data" not in response:
                    raise ScrapelessReaderError(
                        "Scraping SDK response is missing its HTTP envelope."
                    )
                data = response["data"]
                status = response.get("status")
                acknowledgement = _task_acknowledgement(data)
                if isinstance(data, Mapping) and (status in (201, 202) or acknowledgement):
                    task_id = _task_identity(task_id, data.get("taskId", data.get("task_id")))
                check_failure(response)
                _check_result_error(data)
                if status == 200:
                    if acknowledgement:
                        raise ScrapelessReaderError(
                            "Scraping returned an acknowledgement instead of completed content."
                        )
                    return response["data"], task_id
                if status not in (201, 202):
                    raise ScrapelessReaderError("Scraping returned an unexpected HTTP status.")
                if task_id is None:
                    raise ScrapelessReaderError("Pending Scraping task is missing taskId.")
                self._pause(deadline, service, task_id)
                response = self._poll_result(
                    service, task_id, sdk_service.get_task_result, quote(task_id, safe="")
                )
        except ScrapelessReaderError as error:
            if task_id is not None:
                error.task_id = task_id
            raise

    def _ai(
        self, params: dict[str, Any], task_id: str | None, deadline: float
    ) -> tuple[Any, str | None]:
        try:
            sdk_service = self.client.ai_scraper
            if task_id is None:
                response = sdk_service.create_task(params)
            else:
                self._check_deadline(deadline, "ai_scraper", task_id)
                response = self._poll_result(
                    "ai_scraper", task_id, sdk_service.get_task_result, task_id
                )
            while True:
                if not isinstance(response, Mapping):
                    raise ScrapelessReaderError("AI Scraper returned an unexpected response type.")
                task_id = _task_identity(task_id, response.get("task_id"))
                check_failure(response)
                status = response.get("status")
                if not isinstance(status, str):
                    raise ScrapelessReaderError(
                        "AI Scraper returned a missing or malformed task status."
                    )
                if response.get("status") == "success":
                    if "task_result" not in response or response["task_result"] is None:
                        raise ScrapelessReaderError(
                            "Successful AI Scraper task is missing task_result."
                        )
                    return response["task_result"], task_id
                if status not in PENDING:
                    raise ScrapelessReaderError("AI Scraper returned an unknown task status.")
                if not isinstance(task_id, str) or not task_id:
                    raise ScrapelessReaderError("Pending AI Scraper task is missing task_id.")
                self._pause(deadline, "ai_scraper", task_id)
                response = self._poll_result(
                    "ai_scraper", task_id, sdk_service.get_task_result, task_id
                )
        except ScrapelessReaderError as error:
            if task_id is not None:
                error.task_id = task_id
            raise
