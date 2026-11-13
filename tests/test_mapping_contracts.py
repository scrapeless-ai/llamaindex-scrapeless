"""Regressions from real response schemas, with synthetic record contents."""

import json

import pytest

from llamaindex_scrapeless.mapping import documents


def test_search_relative_link_uses_absolute_redirect():
    docs = documents(
        {
            "snippet": "A search snippet.",
            "link": "/goto?url=opaque-value",
            "redirect_link": "https://example.com/source",
        }
    )
    assert docs[0].metadata["url"] == "https://example.com/source"


def test_search_absolute_source_is_preserved():
    docs = documents(
        {
            "snippet": "A search snippet.",
            "link": "https://example.com/source",
            "redirect_link": "https://example.org/redirect",
        }
    )
    assert docs[0].metadata["url"] == "https://example.com/source"


def test_chatgpt_result_text_and_reference_metadata():
    docs = documents(
        {
            "result_text": "A synthetic answer.",
            "model": "example-model",
            "content_references": [{"url": "https://example.com/source"}],
            "search_result": [],
            "links": [],
            "ads": {"text": "Do not index this ad"},
        }
    )
    assert docs[0].text == "A synthetic answer."
    assert docs[0].metadata["model"] == "example-model"
    assert docs[0].metadata["content_references"][0]["url"] == "https://example.com/source"
    assert "content_references" in docs[0].excluded_embed_metadata_keys
    assert "ads" not in docs[0].metadata


@pytest.mark.parametrize("empty", [None, "", "  "])
def test_empty_ai_answer_does_not_index_ads(empty):
    assert documents({"result_text": empty, "ads": {"text": "Not an answer"}}) == []


@pytest.mark.parametrize("value", [{"answer": 42}, ["one", "two"], 0, False])
def test_non_string_result_text_is_preserved_as_structured_data(value):
    record = {"result_text": value, "source": "controlled fixture"}
    docs = documents(record)

    assert len(docs) == 1
    assert json.loads(docs[0].text) == record
    assert docs[0].metadata["content_format"] == "json"


def test_unlocker_html_string_becomes_text():
    docs = documents(
        "<!doctype html><html><body><h1>Example</h1><p>Page content</p>"
        "<script>notContent()</script></body></html>"
    )
    assert docs[0].text == "Example\nPage content"


def test_grok_answer_and_search_references():
    docs = documents(
        {
            "full_response": "A complete synthetic answer.",
            "raw_response": "Raw transport text",
            "user_model": "example-model",
            "user_query": "A question",
            "web_search_results": [{"url": "https://example.com/source"}],
            "x_search_results": [],
            "footnotes": {},
        }
    )
    assert docs[0].text == "A complete synthetic answer."
    assert docs[0].metadata["web_search_results"][0]["url"] == "https://example.com/source"
    assert "web_search_results" in docs[0].excluded_embed_metadata_keys
    assert "content_format" not in docs[0].metadata


def test_alexa_markdown_answer_and_references():
    docs = documents(
        {
            "md_text": "A synthetic answer [1].",
            "raw_text": "Plain answer",
            "references": [{"id": "1", "url": "https://example.com/source"}],
            "sources": [{"text": "Source", "url": "https://example.com/source"}],
            "directives": [{"name": "Do not index transport directives"}],
        }
    )
    assert docs[0].text == "A synthetic answer [1]."
    assert len(docs[0].metadata["references"]) == 1
    assert "references" in docs[0].excluded_llm_metadata_keys


def test_perplexity_web_results_are_preserved():
    docs = documents(
        {
            "result_text": "A sourced answer [1].",
            "web_results": [
                {"index": 1, "name": "Source", "url": "https://example.com/source"},
            ],
        }
    )
    assert len(docs[0].metadata["web_results"]) == 1
    assert "web_results" in docs[0].excluded_embed_metadata_keys
