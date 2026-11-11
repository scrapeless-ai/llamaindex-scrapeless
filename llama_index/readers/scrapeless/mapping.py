"""Convert selected result records, without indexing transport envelopes."""

import inspect
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any

from bs4 import BeautifulSoup
from llama_index.core.schema import Document

from .errors import ScrapelessReaderError

Mapper = Callable[[Any], Document]
Selector = Callable[[Any], Iterable[Any]]
REFERENCE_FIELDS = (
    "citations",
    "sources",
    "search_results",
    "search_result",
    "content_references",
    "links",
    "web_results",
    "web_search_results",
    "x_search_results",
    "references",
    "footnotes",
)


def plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return value


def check_failure(value: Any) -> None:
    """Check a known control envelope, never arbitrary business records."""
    if isinstance(value, Mapping):
        status = value.get("status")
        if value.get("success") is False or (
            isinstance(status, str)
            and status
            in {
                "failed",
                "error",
                "cancelled",
                "canceled",
                "aborted",
                "timeout",
            }
        ):
            # Server messages can echo credentials or submitted headers.
            raise ScrapelessReaderError("Scrapeless reported an unsuccessful result.")


def records(payload: Any, selector: Selector | None = None) -> list[Any]:
    payload = plain(payload)
    if selector is not None:
        selected = selector(payload)
        if inspect.isawaitable(selected):
            if inspect.iscoroutine(selected):
                selected.close()
            raise TypeError("records_selector must return records synchronously.")
        if isinstance(selected, (str, bytes, Mapping)):
            raise TypeError("records_selector must return an iterable of records, not one record.")
        return list(selected)
    if payload is None:
        raise ScrapelessReaderError(
            "Result is missing; use a selector for a custom response schema."
        )
    if isinstance(payload, list):
        return payload
    return [payload]


def default_document(item: Any) -> Document | None:
    item = plain(item)
    if isinstance(item, Document):
        return item
    metadata: dict[str, Any] = {}
    if isinstance(item, str):
        text = item
        if re.match(r"\s*(?:<!doctype\s+html\b|<html\b|<body\b|<main\b)", text, re.I):
            soup = BeautifulSoup(text, "html.parser")
            if soup.find():
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                text = soup.get_text("\n", strip=True)
    elif isinstance(item, Mapping):
        if not item:
            return None
        source = item.get("metadata") or {}
        if not isinstance(source, Mapping):
            source = {}
        for key in ("title", "description", "language", "statusCode"):
            value = source.get(key, item.get(key))
            if value is not None:
                metadata[key] = value
        url = source.get("sourceURL") or source.get("url") or item.get("url") or item.get("link")
        redirect = item.get("redirect_link")
        if (
            isinstance(url, str)
            and url.startswith("/")
            and isinstance(redirect, str)
            and redirect.startswith(("https://", "http://"))
        ):
            # Search results may expose a relative link with a usable absolute redirect.
            url = redirect
        if url:
            metadata["url"] = url
        for key in (*REFERENCE_FIELDS, "model", "user_model"):
            if key in item:
                metadata[key] = item[key]
        text = ""
        for field in (
            "markdown",
            "result_text",
            "full_response",
            "md_text",
            "text",
            "content",
            "answer",
            "response",
            "snippet",
            "raw_text",
        ):
            value = item.get(field)
            if isinstance(value, str) and value.strip():
                text = value
                break
        answer = item.get("result_text")
        if (
            not text
            and "result_text" in item
            and (answer is None or (isinstance(answer, str) and not answer.strip()))
        ):
            # An empty AI answer must not index unrelated ads/products instead.
            return None
        if not text:
            html = item.get("html") or item.get("rawHtml")
            if isinstance(html, str) and html.strip():
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style", "noscript"]):
                    tag.decompose()
                text = soup.get_text("\n", strip=True)
            else:
                text = json.dumps(item, ensure_ascii=False, sort_keys=True)
                metadata["content_format"] = "json"
    else:
        raise TypeError("Records must be dictionaries, strings, dataclasses, or Documents.")
    if not text.strip():
        return None
    return Document(text=text, metadata=metadata)


def documents(
    payload: Any,
    *,
    mapper: Mapper | None = None,
    selector: Selector | None = None,
    metadata: dict[str, Any] | None = None,
) -> list[Document]:
    output = []
    for item in records(payload, selector):
        doc = mapper(item) if mapper is not None else default_document(item)
        if inspect.isawaitable(doc):
            if inspect.iscoroutine(doc):
                doc.close()
            raise TypeError("mapping_function must return a Document synchronously.")
        if doc is None and mapper is None:
            continue
        if not isinstance(doc, Document):
            raise TypeError("mapping_function must return a LlamaIndex Document.")
        # A mapper may reuse a Document; leave the caller's object unchanged.
        doc = doc.model_copy(deep=True)
        doc.metadata = {**(metadata or {}), **doc.metadata}
        for key in ("task_id", *REFERENCE_FIELDS):
            if key in doc.metadata:
                if key not in doc.excluded_embed_metadata_keys:
                    doc.excluded_embed_metadata_keys.append(key)
                if key not in doc.excluded_llm_metadata_keys:
                    doc.excluded_llm_metadata_keys.append(key)
        output.append(doc)
    return output
