"""Keep the original remote task available when a result cannot be accepted."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from llamaindex_scrapeless import ScrapelessReader, ScrapelessReaderError


@pytest.mark.parametrize(
    "result",
    [
        {"status": "failed", "data": []},
        {"status": "completed"},
        {"status": "completed", "data": [{"metadata": {"statusCode": 403}}]},
        {"status": "completed", "data": [None]},
        {"status": "completed", "data": [42]},
        {"status": "completed", "data": [{"metadata": {"statusCode": "403"}}]},
    ],
)
def test_crawl_result_error_retains_submitted_task(result):
    sdk = Mock()
    sdk.request.side_effect = [{"id": "original-crawl"}, result]
    reader = ScrapelessReader(client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk)))

    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data("batch_scrape", params={"urls": ["https://example.com"]})

    assert error.value.service == "batch_scrape"
    assert error.value.task_id == "original-crawl"
    assert [call.args[1] for call in sdk.request.call_args_list] == ["POST", "GET"]


@pytest.mark.parametrize("cursor", ["https://[", {}, 0, " https://api.scrapeless.com"])
def test_invalid_cursor_retains_submitted_task_without_another_request(cursor):
    sdk = Mock(base_url="https://api.scrapeless.com")
    sdk.request.side_effect = [
        {"id": "original-crawl"},
        {"status": "completed", "data": [], "next": cursor},
    ]
    reader = ScrapelessReader(client=SimpleNamespace(scraping_crawl=SimpleNamespace(crawl=sdk)))

    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data("crawl", params={"url": "https://example.com"})

    assert error.value.service == "crawl"
    assert error.value.task_id == "original-crawl"
    assert [call.args[1] for call in sdk.request.call_args_list] == ["POST", "GET"]


def test_crawl_pagination_error_retains_resumed_task():
    sdk = Mock()
    sdk.base_url = "https://api.scrapeless.com"
    sdk.request.side_effect = [
        {
            "status": "completed",
            "data": [{"text": "First page"}],
            "next": "/api/v1/crawler/crawl/existing-crawl?skip=1",
        },
        {"status": "failed", "data": []},
    ]
    reader = ScrapelessReader(client=SimpleNamespace(scraping_crawl=SimpleNamespace(crawl=sdk)))

    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data("crawl", task_id="existing-crawl")

    assert error.value.service == "crawl"
    assert error.value.task_id == "existing-crawl"
    assert [call.args[1] for call in sdk.request.call_args_list] == ["GET", "GET"]


@pytest.mark.parametrize("argument", ["mapping_function", "records_selector"])
def test_callback_type_error_is_not_reported_as_service_failure(argument):
    sdk = Mock()
    sdk.request.return_value = {"status": "completed", "data": [{"text": "Usable page"}]}
    reader = ScrapelessReader(client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk)))

    callback = Mock(side_effect=TypeError("Controlled callback error"))
    with pytest.raises(TypeError, match="Controlled callback error"):
        reader.load_data("batch_scrape", task_id="existing", **{argument: callback})
    callback.assert_called_once()


@pytest.mark.parametrize("result", [None, {"status": "failed"}, {"unexpected": "schema"}])
def test_search_rejected_completion_retains_task_id(result):
    sdk = Mock()
    sdk.create_task.return_value = {"status": 201, "data": {"taskId": "original-search"}}
    sdk.get_task_result.return_value = {"status": 200, "data": result}
    reader = ScrapelessReader(client=SimpleNamespace(deepserp=sdk), poll_interval=0.001)

    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data("google_search", params={"input": {"q": "example"}})

    assert error.value.service == "google_search"
    assert error.value.task_id == "original-search"
    sdk.create_task.assert_called_once()
    sdk.get_task_result.assert_called_once_with("original-search")


def test_ai_failure_keeps_id_from_control_envelope():
    sdk = Mock()
    sdk.create_task.return_value = {"status": "failed", "task_id": "original-ai"}
    reader = ScrapelessReader(client=SimpleNamespace(ai_scraper=sdk))

    with pytest.raises(ScrapelessReaderError) as error:
        reader.load_data("ai_scraper", params={"actor": "scraper.chatgpt", "input": {}})

    assert error.value.service == "ai_scraper"
    assert error.value.task_id == "original-ai"
    sdk.create_task.assert_called_once()
    sdk.get_task_result.assert_not_called()


def test_mismatched_task_id_does_not_replace_known_identity():
    sdk = Mock()
    sdk.get_task_result.return_value = {"status": "failed", "task_id": "different-ai"}
    reader = ScrapelessReader(client=SimpleNamespace(ai_scraper=sdk))

    with pytest.raises(ScrapelessReaderError, match="does not match") as error:
        reader.load_data("ai_scraper", task_id="original-ai")

    assert error.value.task_id == "original-ai"
    sdk.create_task.assert_not_called()
