"""Copilot-specific wire and response contracts through the installed SDK."""

import json
from types import SimpleNamespace

import pytest
from scrapeless import Scrapeless
from scrapeless.error import ScrapelessError

from llamaindex_scrapeless import ScrapelessReader, ScrapelessTaskError

PAYLOAD = {
    "actor": "scraper.copilot",
    "input": {"prompt": "Explain photosynthesis.", "country": "US", "mode": "chat"},
}


def install_http(monkeypatch, replies):
    replies = iter(replies)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        status, data = next(replies)
        return SimpleNamespace(
            ok=status < 400,
            status_code=status,
            headers={"Content-Type": "application/json"},
            json=lambda: data,
            text=json.dumps(data),
        )

    monkeypatch.setattr("requests.request", request)
    return calls


@pytest.mark.parametrize("mode", ["chat", "search", "smart", "reasoning", "study"])
def test_reader_forwards_copilot_mode_without_rewriting(monkeypatch, mode):
    payload = {"actor": "scraper.copilot", "input": {**PAYLOAD["input"], "mode": mode}}
    calls = install_http(
        monkeypatch,
        [
            (201, {"status": "pending", "task_id": "copilot-job"}),
            (202, {"status": "running"}),
            (
                200,
                {
                    "status": "success",
                    "task_result": {
                        "result_text": "Plants use sunlight.",
                        "mode": mode,
                        "citations": [{"title": "A source", "url": "https://example.com/source"}],
                        "links": ["https://example.com/source"],
                    },
                },
            ),
        ],
    )
    client = Scrapeless({"api_key": "YOUR_SCRAPELESS_API_KEY"})
    docs = ScrapelessReader(client=client, poll_interval=0.001).load_data(
        "ai_scraper", params=payload
    )
    assert calls[0][2]["json"] == payload
    assert calls[0][1].endswith("/api/v2/scraper/request")
    assert all(call[1].endswith("/api/v2/scraper/result/copilot-job") for call in calls[1:])
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
    assert docs[0].text == "Plants use sunlight."
    assert docs[0].metadata["actor"] == "scraper.copilot"
    assert len(docs[0].metadata["citations"]) == 1
    assert "citations" in docs[0].excluded_embed_metadata_keys


def test_sdk_created_task_can_be_completed_by_reader_without_resubmission(monkeypatch):
    calls = install_http(
        monkeypatch,
        [
            (201, {"status": "pending", "task_id": "same-job"}),
            (200, {"status": "success", "task_result": {"result_text": "An answer."}}),
        ],
    )
    client = Scrapeless({"api_key": "YOUR_SCRAPELESS_API_KEY"})
    task = client.ai_scraper.create_task(PAYLOAD)
    docs = ScrapelessReader(client=client).load_data(
        "ai_scraper", task_id=task["task_id"], params=PAYLOAD
    )
    assert docs[0].text == "An answer."
    assert [call[0] for call in calls] == ["POST", "GET"]


def test_copilot_failed_http400_is_not_a_mapping_failure_or_retried_submission(monkeypatch):
    calls = install_http(
        monkeypatch,
        [
            (201, {"status": "pending", "task_id": "failed-job"}),
            (202, {"status": "running"}),
            (400, {"status": "failed", "message": "Execution failed. Please try again later."}),
        ],
    )
    client = Scrapeless({"api_key": "YOUR_SCRAPELESS_API_KEY"})
    mapped = []
    with pytest.raises(ScrapelessTaskError) as error:
        ScrapelessReader(client=client, poll_interval=0.001).load_data(
            "ai_scraper", params=PAYLOAD, mapping_function=lambda item: mapped.append(item)
        )
    assert error.value.task_id == "failed-job"
    assert isinstance(error.value.__cause__, ScrapelessError)
    assert mapped == []
    assert [call[0] for call in calls] == ["POST", "GET", "GET"]
