"""Independent cases found during the second implementation audit."""

import json
from types import SimpleNamespace

import pytest
from llama_index.core import Document

from llama_index.readers.scrapeless import (
    ScrapelessReader,
    ScrapelessReaderError,
    ScrapelessTaskError,
)
from llama_index.readers.scrapeless.mapping import documents


@pytest.mark.parametrize(
    "business_record",
    [
        {"text": "A failed payment record", "status": "failed"},
        {"text": "A pending order", "status": {"code": "pending"}},
        {"text": "An unsuccessful experiment", "success": False},
    ],
)
def test_domain_fields_are_not_task_envelopes(business_record):
    result = documents([business_record])
    assert result[0].text == business_record["text"]


def test_custom_mapper_can_read_domain_status():
    result = documents(
        {"status": "failed", "amount": 7},
        mapper=lambda record: Document(text=json.dumps(record)),
    )
    assert json.loads(result[0].text)["status"] == "failed"


@pytest.mark.parametrize(
    "text",
    [
        "Use `<p>` for a paragraph and `<code>` for inline code.",
        "The expression a < b and c > d is true.",
        "A <custom>literal</custom> token is part of this text.",
    ],
)
def test_plain_text_is_not_rewritten_as_html(text):
    assert documents(text)[0].text == text


@pytest.mark.parametrize("status", [[], {}, True, 42])
@pytest.mark.parametrize("service", ["scrape", "ai_scraper"])
def test_malformed_control_status_is_a_reader_error(status, service):
    if service == "scrape":
        sdk_service = SimpleNamespace(request=lambda *args: {"status": status})
        client = SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk_service))
    else:
        client = SimpleNamespace(
            ai_scraper=SimpleNamespace(get_task_result=lambda task_id: {"status": status})
        )
    with pytest.raises(ScrapelessReaderError):
        ScrapelessReader(client=client).load_data(service, task_id="existing")


def test_scraping_accepts_http202_while_existing_task_is_running():
    responses = iter(
        [
            {"status": 202, "data": {"taskId": "job"}},
            {"status": 200, "data": [{"text": "Finished"}]},
        ]
    )
    client = SimpleNamespace(scraping=SimpleNamespace(get_task_result=lambda _: next(responses)))
    docs = ScrapelessReader(client=client, poll_interval=0.001).load_data("scraping", task_id="job")
    assert docs[0].text == "Finished"


def test_scraping_does_not_index_http200_task_acknowledgement():
    client = SimpleNamespace(
        scraping=SimpleNamespace(
            get_task_result=lambda _: {
                "status": 200,
                "data": {"taskId": "job", "message": "task in progress"},
            }
        )
    )
    with pytest.raises(ScrapelessReaderError):
        ScrapelessReader(client=client).load_data("scraping", task_id="job")


@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "error": "Service failed"},
        {"status": "running"},
    ],
)
def test_http200_does_not_hide_error_or_unfinished_envelope(payload):
    client = SimpleNamespace(
        scraping=SimpleNamespace(
            get_task_result=lambda _: {
                "status": 200,
                "data": payload,
            }
        )
    )
    with pytest.raises(ScrapelessReaderError):
        ScrapelessReader(client=client).load_data("scraping", task_id="job")


def test_structured_scraping_preserves_domain_status_and_success():
    data = {"status": {"code": "failed"}, "success": False, "amount": 7}
    client = SimpleNamespace(
        scraping=SimpleNamespace(
            get_task_result=lambda _: {
                "status": 200,
                "data": data,
            }
        )
    )
    docs = ScrapelessReader(client=client).load_data("scraping", task_id="job")
    assert json.loads(docs[0].text) == data


def test_ai_does_not_silently_change_task_identity():
    client = SimpleNamespace(
        ai_scraper=SimpleNamespace(
            get_task_result=lambda _: {
                "status": "success",
                "task_id": "different-task",
                "task_result": {"text": "Wrong task"},
            }
        )
    )
    with pytest.raises(ScrapelessReaderError):
        ScrapelessReader(client=client).load_data("ai_scraper", task_id="original-task")


def test_v1_task_id_is_quoted_before_sdk_path_concatenation():
    captured = []

    def get_result(task_id):
        captured.append(task_id)
        return {"status": 200, "data": {"text": "Done"}}

    reader = ScrapelessReader(
        client=SimpleNamespace(scraping=SimpleNamespace(get_task_result=get_result))
    )
    reader.load_data("scraping", task_id="job/part?x=y")
    assert captured == ["job%2Fpart%3Fx%3Dy"]


@pytest.mark.parametrize(
    "params",
    [
        {"url": ""},
        {"url": None},
        {"url": 123},
        {"url": "file:///tmp/data"},
        {"url": "https://example.com", "options": []},
    ],
)
def test_bad_crawl_inputs_fail_before_sdk_request(params):
    client = SimpleNamespace(
        scraping_crawl=SimpleNamespace(
            scrape=SimpleNamespace(
                request=lambda *args: pytest.fail("Must validate before SDK submission")
            )
        )
    )
    with pytest.raises(ValueError):
        ScrapelessReader(client=client).load_data("scrape", params=params)


@pytest.mark.parametrize("value", ["30", None, {}, []])
def test_invalid_timeout_type_has_consistent_error(value):
    with pytest.raises(ValueError):
        ScrapelessReader(client=object(), timeout=value)


@pytest.mark.parametrize("keyword", ["mapping_function", "records_selector"])
def test_async_mapping_rejected_before_sdk_request(keyword):
    async def function(record):
        return record

    with pytest.raises(ValueError, match="synchronous"):
        ScrapelessReader(client=object()).load_data("scrape", **{keyword: function})


def test_awaitable_mapper_results_are_closed_without_warning():
    async def make_doc():
        return Document(text="Async")

    with pytest.raises(TypeError, match="synchronously"):
        documents([{}], mapper=lambda item: make_doc())
    with pytest.raises(TypeError, match="synchronously"):
        documents([{}], selector=lambda payload: make_doc())


def test_dataclass_record_is_mapped_with_source_metadata():
    from scrapeless.types import ScrapingCrawlDocument, ScrapingCrawlDocumentMetadata

    docs = documents(
        [
            ScrapingCrawlDocument(
                markdown="Data",
                metadata=ScrapingCrawlDocumentMetadata(sourceURL="https://example.com"),
            )
        ]
    )
    assert docs[0].text == "Data"
    assert docs[0].metadata["url"] == "https://example.com"


@pytest.mark.parametrize("service", ["ai_scraper", "scraping", "google_search", "web_unlocker"])
@pytest.mark.parametrize(
    "params", [{}, {"actor": "", "input": {}}, {"actor": "scraper.example", "input": []}]
)
def test_invalid_actor_requests_fail_before_sdk_access(service, params):
    with pytest.raises(ValueError):
        ScrapelessReader(client=object()).load_data(service, params=params)


def test_polling_error_retains_created_task_and_original_exception():
    calls = []
    original = RuntimeError("upstream temporarily unavailable")

    def request(endpoint, method, *args):
        calls.append(method)
        if method == "POST":
            return {"id": "accepted-task"}
        raise original

    reader = ScrapelessReader(
        client=SimpleNamespace(
            scraping_crawl=SimpleNamespace(scrape=SimpleNamespace(request=request))
        )
    )
    with pytest.raises(ScrapelessTaskError) as error:
        reader.load_data("scrape", params={"url": "https://example.com"})
    assert error.value.task_id == "accepted-task"
    assert error.value.service == "scrape"
    assert error.value.__cause__ is original
    assert calls == ["POST", "GET"]
