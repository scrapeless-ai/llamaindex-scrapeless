"""Check completion and limit behavior without contacting external services."""

from types import SimpleNamespace

import pytest

from llamaindex_scrapeless import (
    ScrapelessReader,
    ScrapelessReaderError,
    ScrapelessTaskTimeout,
)


def test_partial_crawl_page_error_is_not_silent_success():
    service = SimpleNamespace(
        request=lambda *args: {
            "status": "completed",
            "data": [
                {"text": "Good", "metadata": {"statusCode": 200}},
                {"text": "Not found", "metadata": {"statusCode": 404}},
            ],
        }
    )
    reader = ScrapelessReader(client=SimpleNamespace(scraping_crawl=SimpleNamespace(crawl=service)))
    with pytest.raises(ScrapelessReaderError, match="page contains an error"):
        reader.load_data("crawl", task_id="existing")


def test_unlocker_acknowledgement_is_not_a_document():
    client = SimpleNamespace(universal=SimpleNamespace(scrape=lambda params: {"taskId": "pending"}))
    reader = ScrapelessReader(client=client)
    with pytest.raises(ScrapelessReaderError, match="acknowledgement"):
        reader.load_data("web_unlocker", params={"actor": "unlocker.webunlocker", "input": {}})


def test_crawl_timeout_includes_reusable_id(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("llamaindex_scrapeless.base.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "llamaindex_scrapeless.base.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    service = SimpleNamespace(request=lambda *args: {"status": "scraping"})
    reader = ScrapelessReader(
        client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=service)),
        timeout=1,
        poll_interval=1,
    )
    with pytest.raises(ScrapelessTaskTimeout) as error:
        reader.load_data("scrape", task_id="existing")
    assert error.value.task_id == "existing"
