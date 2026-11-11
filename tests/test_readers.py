"""Exercise adapters against the installed SDK with controlled HTTP responses."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from scrapeless import Scrapeless

from llama_index.readers.scrapeless import (
    ScrapelessReader,
    ScrapelessReaderError,
    ScrapelessTaskTimeout,
)
from llama_index.readers.scrapeless.mapping import documents


@pytest.fixture
def http(monkeypatch):
    responses, calls = [], []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert responses, f"Unexpected SDK request: {method} {url}"
        status, body = responses.pop(0)
        return SimpleNamespace(
            ok=status < 400,
            status_code=status,
            headers={"Content-Type": "application/json"},
            json=lambda: body,
            text=json.dumps(body),
        )

    monkeypatch.setattr("requests.request", request)
    yield responses, calls
    assert not responses, "Not all expected API responses were consumed."


@pytest.fixture
def client():
    return Scrapeless({"api_key": "YOUR_SCRAPELESS_API_KEY", "timeout": 1000})


@pytest.fixture
def reader(client):
    return ScrapelessReader(client=client, timeout=1, poll_interval=0.001)


def test_scrape_real_sdk_contract(reader, http):
    queue, calls = http
    queue.extend(
        [
            (200, {"success": True, "id": "page-job"}),
            (200, {"status": "scraping"}),
            (
                200,
                {
                    "status": "completed",
                    "success": True,
                    "data": {
                        "markdown": "# Example\nA useful page.",
                        "metadata": {"sourceURL": "https://example.com", "title": "Example"},
                    },
                },
            ),
        ]
    )
    docs = reader.load_data(
        params={
            "url": "https://example.com",
            "options": {
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
        }
    )
    assert len(docs) == 1
    assert docs[0].text.startswith("# Example")
    assert docs[0].metadata["url"] == "https://example.com"
    assert docs[0].metadata["task_id"] == "page-job"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/api/v1/crawler/scrape")
    assert calls[0][2]["json"]["formats"] == ["markdown"]
    assert "includeTags" not in calls[0][2]["json"]
    assert "timeout" not in calls[0][2]["json"]


@pytest.mark.parametrize("absolute", [False, True])
def test_crawl_collects_all_pages(reader, http, absolute):
    queue, calls = http
    path = "/api/v1/crawler/crawl/site-job?skip=1"
    queue.extend(
        [
            (200, {"id": "site-job"}),
            (
                200,
                {
                    "status": "completed",
                    "data": [{"text": "Page one"}],
                    "next": "https://api.scrapeless.com" + path if absolute else path,
                },
            ),
            (200, {"status": "completed", "data": [{"text": "Page two"}]}),
        ]
    )
    docs = reader.load_data(
        "crawl",
        params={
            "url": "https://example.com",
            "options": {
                "limit": 2,
                "scrapeOptions": {"formats": ["markdown"]},
            },
        },
    )
    assert [d.text for d in docs] == ["Page one", "Page two"]
    assert calls[0][2]["json"]["scrapeOptions"]["formats"] == ["markdown"]
    assert "excludeTags" not in calls[0][2]["json"]["scrapeOptions"]
    assert "allowExternalLinks" not in calls[0][2]["json"]
    assert calls[-1][1] == "https://api.scrapeless.com" + path


def test_batch_uses_sdk_and_maps_every_page(reader, http):
    queue, calls = http
    queue.extend(
        [
            (200, {"id": "batch-job"}),
            (200, {"status": "completed", "data": [{"text": "A"}, {"text": "B"}]}),
        ]
    )
    docs = reader.load_data(
        "batch_scrape",
        params={
            "urls": [
                "https://example.com/a",
                "https://example.com/b",
            ]
        },
    )
    assert [d.text for d in docs] == ["A", "B"]
    assert calls[0][1].endswith("/scrape/batch")
    assert calls[0][2]["json"]["ignoreInvalidURLs"] is False


@pytest.mark.parametrize("service", ["google_search", "scraping"])
@pytest.mark.parametrize("pending", [False, True])
def test_scraping_immediate_and_deferred(reader, http, service, pending):
    queue, calls = http
    result = {
        "organic_results": [
            {"title": "Example", "snippet": "A summary", "link": "https://example.com/article"}
        ]
    }
    if pending:
        queue.extend([(201, {"taskId": "search-job"}), (200, result)])
    else:
        queue.append((200, result))
    docs = reader.load_data(
        service,
        params={
            "actor": "scraper.google.search",
            "input": {
                "q": "example topic",
                "gl": "us",
                "hl": "en",
            },
        },
    )
    assert calls[0][2]["json"]["async"] is True
    assert docs[0].metadata["q"] == "example topic"
    if service == "google_search":
        assert docs[0].text == "A summary"
        assert docs[0].metadata["content_kind"] == "search_result"
        assert docs[0].metadata["url"] == "https://example.com/article"
    else:
        assert docs[0].metadata["content_format"] == "json"
        assert json.loads(docs[0].text) == result


def test_search_default_actor_and_empty_results(reader, http):
    queue, calls = http
    queue.append((200, {"organic_results": []}))
    assert reader.load_data("google_search", params={"input": {"q": "example"}}) == []
    assert calls[0][2]["json"]["actor"] == "scraper.google.search"


def test_search_custom_vertical_selector(reader, http):
    queue, _ = http
    queue.append((200, {"news_results": [{"text": "News"}]}))
    docs = reader.load_data(
        "google_search",
        params={"input": {"q": "example"}},
        records_selector=lambda data: data["news_results"],
    )
    assert docs[0].text == "News"


@pytest.mark.parametrize("initial", ["pending", "running", "success"])
def test_ai_task_completion_and_context(reader, http, initial):
    queue, calls = http
    complete = {
        "status": "success",
        "task_result": {
            "text": "An AI answer",
            "citations": [{"url": "https://example.com"}],
        },
    }
    if initial != "success":
        queue.extend([(200, {"task_id": "ai-job", "status": initial}), (200, complete)])
    else:
        queue.append((200, {"task_id": "ai-job", **complete}))
    docs = reader.load_data(
        "ai_scraper",
        params={
            "actor": "scraper.chatgpt",
            "input": {
                "prompt": "Explain retrieval",
                "country": "US",
                "shopping": False,
            },
        },
    )
    assert docs[0].text == "An AI answer"
    assert docs[0].metadata["actor"] == "scraper.chatgpt"
    assert docs[0].metadata["prompt"] == "Explain retrieval"
    assert docs[0].metadata["citations"][0]["url"] == "https://example.com"
    assert "citations" in docs[0].excluded_embed_metadata_keys
    assert calls[0][2]["headers"]["x-api-token"] == "YOUR_SCRAPELESS_API_KEY"
    assert calls[0][2]["json"]["input"]["shopping"] is False


def test_ai_valid_empty_result(reader, http):
    queue, _ = http
    queue.append((200, {"status": "success", "task_result": {}}))
    assert reader.load_data("ai_scraper", params={"actor": "scraper.overview", "input": {}}) == []


def test_unlocker_html_mapping(reader, http):
    queue, calls = http
    queue.append(
        (
            200,
            {
                "data": {
                    "html": "<main><h1>Title</h1><p>Body</p><script>secretScript()</script></main>"
                }
            },
        )
    )
    docs = reader.load_data(
        "web_unlocker",
        params={
            "actor": "unlocker.webunlocker",
            "input": {"url": "https://example.com", "method": "GET"},
        },
    )
    assert docs[0].text == "Title\nBody"
    assert docs[0].metadata["url"] == "https://example.com"
    assert calls[0][1].endswith("/api/v1/unlocker/request")


@pytest.mark.parametrize(
    "service,result",
    [
        ("ai_scraper", {"status": "failed", "message": "Sensitive server detail"}),
        ("ai_scraper", {"status": "success"}),
        ("ai_scraper", {"status": "unknown"}),
        ("ai_scraper", {"status": "running"}),
        ("scrape", {"status": "failed"}),
        ("scrape", {"status": "completed"}),
        ("scrape", {"status": "unknown"}),
        ("google_search", {"unexpected": []}),
    ],
)
def test_malformed_or_failed_tasks_are_not_documents(reader, http, service, result):
    queue, _ = http
    if service == "scrape":
        queue.extend([(200, {"id": "job"}), (200, result)])
    else:
        queue.append((200, result))
    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data(
            service,
            params=(
                {"actor": "scraper.chatgpt", "input": {}}
                if service == "ai_scraper"
                else {"input": {}}
                if service == "google_search"
                else {"url": "https://example.com"}
            ),
        )
    assert "Sensitive server detail" not in str(error.value)


def test_timeout_then_resume_does_not_submit_again(client, http, monkeypatch):
    queue, calls = http
    clock = [0.0]
    monkeypatch.setattr("llama_index.readers.scrapeless.base.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "llama_index.readers.scrapeless.base.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    reader = ScrapelessReader(client=client, timeout=1, poll_interval=1)
    queue.append((200, {"status": "running", "task_id": "resume-me"}))
    with pytest.raises(ScrapelessTaskTimeout) as error:
        reader.load_data("ai_scraper", params={"actor": "scraper.chatgpt", "input": {}})
    assert error.value.task_id == "resume-me"
    queue.append((200, {"status": "success", "task_result": {"text": "Finished"}}))
    docs = reader.load_data("ai_scraper", task_id=error.value.task_id)
    assert docs[0].text == "Finished"
    assert [call[0] for call in calls] == ["POST", "GET"]


@pytest.mark.parametrize(
    "next_url",
    [
        "https://other.example/api/v1/crawler/crawl/job?skip=1",
        "//other.example/api/v1/crawler/crawl/job?skip=1",
        "/api/v1/crawler/crawl/another-job?skip=1",
        "/api/v1/crawler/crawl/job?skip=1#fragment",
    ],
)
def test_rejects_unsafe_crawl_cursor(reader, http, next_url):
    queue, calls = http
    queue.append((200, {"status": "completed", "data": [], "next": next_url}))
    with pytest.raises(ScrapelessReaderError):
        reader.load_data("crawl", task_id="job")
    assert len(calls) == 1


def test_repeated_crawl_cursor_is_failure(reader, http):
    queue, _ = http
    body = {"status": "completed", "data": [], "next": "/api/v1/crawler/crawl/job?skip=1"}
    queue.extend([(200, body), (200, body)])
    with pytest.raises(ScrapelessReaderError, match="repeated"):
        reader.load_data("crawl", task_id="job")


def test_async_reader_uses_sdk_in_thread(reader, http):
    queue, _ = http
    queue.append((200, {"status": "success", "task_result": {"text": "Async"}}))
    docs = asyncio.run(reader.aload_data("ai_scraper", task_id="existing"))
    assert docs[0].text == "Async"


@pytest.mark.parametrize(
    "field,value",
    [
        ("timeout", 0),
        ("poll_interval", -1),
        ("request_timeout", float("nan")),
        ("timeout", float("inf")),
        ("timeout", True),
    ],
)
def test_invalid_timing(client, field, value):
    with pytest.raises(ValueError):
        ScrapelessReader(client=client, **{field: value})


def test_client_config_and_credential_precedence(monkeypatch):
    monkeypatch.setenv("SCRAPELESS_API_KEY", "YOUR_SCRAPELESS_API_KEY")
    reader = ScrapelessReader(api_key="YOUR_API_KEY", request_timeout=7)
    assert reader.client.ai_scraper.api_key == "YOUR_API_KEY"
    assert reader.client.ai_scraper.timeout == 7
    assert ScrapelessReader().client.ai_scraper.api_key == "YOUR_SCRAPELESS_API_KEY"
    with pytest.raises(ValueError):
        ScrapelessReader(client=reader.client, api_key="YOUR_API_KEY")


@pytest.mark.parametrize(
    "call",
    [
        lambda r: r.load_data("not-a-service"),
        lambda r: r.load_data("scrape", task_id=""),
        lambda r: r.load_data("web_unlocker", task_id="existing"),
        lambda r: r.load_data("scrape", params={"url": "https://example.com", "limit": 3}),
        lambda r: r.load_data("batch_scrape", params={"urls": []}),
    ],
)
def test_invalid_arguments_fail_before_network(reader, call):
    with pytest.raises(ValueError):
        call(reader)


def test_default_mapping_preserves_json_and_custom_fields():
    docs = documents([{"price": 12, "currency": "USD"}])
    assert json.loads(docs[0].text) == {"price": 12, "currency": "USD"}
    assert docs[0].metadata["content_format"] == "json"
    assert documents([{}, "  "]) == []
    with pytest.raises(TypeError):
        documents([123])
    with pytest.raises(TypeError):
        documents({}, selector=lambda payload: payload)
