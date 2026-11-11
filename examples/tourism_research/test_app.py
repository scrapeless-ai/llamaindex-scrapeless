"""Controlled research application checks; no live APIs or real source statistics."""

import asyncio
import importlib.util
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from llama_index.core import Document
from llama_index.core.base.llms.types import ChatMessage, ToolCallBlock
from llama_index.core.llms.mock import MockFunctionCallingLLM

SPEC = importlib.util.spec_from_file_location("tourism_app", Path(__file__).with_name("app.py"))
app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(app)


def fixtures():
    return [
        Document(
            text=(
                "Synthetic controlled fixture about tourism visits hotel occupancy accommodation "
                "supply and methodology. This is not a real tourism observation. " * 3
            ),
            metadata={"url": f"https://example.com/source-{i}", "task_id": "controlled-task"},
            excluded_llm_metadata_keys=["task_id"],
            excluded_embed_metadata_keys=["task_id"],
        )
        for i in range(3)
    ]


def test_missing_requested_page_is_rejected():
    with pytest.raises(ValueError, match="Missing requested sources"):
        app.validate_documents(fixtures(), ["https://example.com/missing"])


def test_extra_source_is_rejected_even_when_all_requested_sources_exist():
    docs = fixtures()
    with pytest.raises(ValueError, match="Unrequested source"):
        app.validate_documents(docs, [docs[0].metadata["url"], docs[1].metadata["url"]])


def test_query_values_remain_part_of_source_identity():
    assert app.normalized_url("https://example.com/?q=a/") != app.normalized_url(
        "https://example.com/?q=a"
    )


@pytest.mark.parametrize("url", ["https:opaque", "https://", "https://user:password@example.com"])
def test_malformed_source_urls_are_rejected_before_submission(tmp_path, url):
    with patch.object(app, "ScrapelessReader") as reader:
        with pytest.raises(ValueError, match=r"valid HTTP\(S\)"):
            app.collect(tmp_path / "documents.json", [url])
        reader.assert_not_called()


def test_reloaded_corpus_with_extra_sources_cannot_reach_retrieval(tmp_path):
    docs = fixtures()
    path = tmp_path / "documents.json"
    app.write_json(
        path,
        {
            "schema_version": 1,
            "validation": "passed",
            "requested_urls": [doc.metadata["url"] for doc in docs[:2]],
            "documents": [doc.to_dict() for doc in docs],
        },
    )
    with pytest.raises(ValueError, match="Unrequested source"):
        app.load_documents(path)


def test_source_aliases_use_one_catalogue_entry():
    original = fixtures()[0]
    alias = Document(text=original.text, metadata={"url": original.metadata["url"] + "/#part"})
    assert list(app.build_retriever([original, alias])) == [app.source_id(original.metadata["url"])]


def test_source_id_collision_cannot_hide_a_required_source():
    with patch.object(app, "source_id", return_value="src-00000000"):
        with pytest.raises(ValueError, match="same source ID"):
            app.build_retriever(fixtures())


def test_headline_context_survives_a_methodology_focused_query():
    doc = Document(
        text=(
            "# Synthetic overview\nA fictional headline metric is 17 units.\n"
            + "Methodology caveat and survey coverage. " * 100
        ),
        metadata={"url": "https://example.com/survey"},
    )
    retrievers = app.build_retriever([doc])
    passages = app.search_passages(
        retrievers, "methodology survey coverage", app.source_id(doc.metadata["url"])
    )
    assert any("17 units" in passage["text"] for passage in passages)


def test_release_date_is_available_even_when_search_passages_omit_header():
    doc = Document(text="# Tourism\n\n### Released on:\n\n16 October 2026\n\nReport content.")
    assert app.publication_date(doc) == "16 October 2026"
    assert app.publication_date(Document(text="An undated page.")) is None


def test_partial_batch_recovery_reuses_success_and_repairs_only_failed_page():
    good = {
        "markdown": fixtures()[0].text,
        "metadata": {"url": "https://example.com/good", "statusCode": 200},
    }
    failed = {
        "markdown": "",
        "metadata": {"url": "https://example.com/failed", "statusCode": 403, "error": "Forbidden"},
    }
    fixed = Document(text=fixtures()[0].text, metadata={"url": "https://example.com/failed"})
    reader = Mock()
    reader.load_data.return_value = [fixed]
    error = app.ScrapelessCrawlPageError([good, failed], [1])
    error.task_id = "original-task"
    docs, repairs = app.recover_pages(
        reader, error, ["https://example.com/good", fixed.metadata["url"]]
    )
    assert {d.metadata["url"] for d in docs} == {
        "https://example.com/good",
        "https://example.com/failed",
    }
    reader.load_data.assert_called_once()
    assert reader.load_data.call_args.args == ("web_unlocker",)
    assert repairs[0]["initial_page_status"] == 403


def test_checkpoint_prevents_accidental_new_submission(tmp_path):
    output = tmp_path / "documents.json"
    app.write_json(output.with_suffix(".task.json"), {"task_id": "existing", "urls": app.URLS})
    with patch.object(app, "ScrapelessReader") as reader:
        with pytest.raises(ValueError, match="Resume the checkpoint"):
            app.collect(output, app.URLS)
        reader.assert_not_called()


def test_new_batch_records_id_before_reader_polling(tmp_path):
    output = tmp_path / "documents.json"
    docs = fixtures()
    urls = [doc.metadata["url"] for doc in docs]
    reader = Mock()
    reader.client.scraping_crawl.scrape.request.return_value = {"id": "new-original-task"}

    def checked_poll(service, *, params, task_id):
        assert output.with_suffix(".task.json").exists()
        assert task_id == "new-original-task"
        return docs

    reader.load_data.side_effect = checked_poll
    with patch.object(app, "ScrapelessReader", return_value=reader):
        corpus = app.collect(output, urls)
    assert corpus["validation"] == "passed"
    assert corpus["task_id"] == "new-original-task"
    reader.client.scraping_crawl.scrape.request.assert_called_once()


def test_recovery_uses_failed_page_from_second_cursor(tmp_path):
    output = tmp_path / "documents.json"
    urls = [doc.metadata["url"] for doc in fixtures()[:2]]
    endpoint = "/api/v1/crawler/scrape/batch/original-task"
    sdk = Mock(base_url="https://api.scrapeless.com")
    sdk.request.side_effect = [
        {"id": "original-task"},
        {
            "status": "completed",
            "data": [{"markdown": fixtures()[0].text, "metadata": {"url": urls[0]}}],
            "next": endpoint + "?skip=1",
        },
        {"status": "completed", "data": [{"metadata": {"url": urls[1], "statusCode": 403}}]},
    ]
    unlocker = Mock()
    unlocker.scrape.return_value = fixtures()[1].text
    reader = app.ScrapelessReader(
        client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk), universal=unlocker)
    )
    with patch.object(app, "ScrapelessReader", return_value=reader):
        result = app.collect(output, urls)
    assert result["validation"] == "passed"
    assert result["repairs"][0]["url"] == urls[1]
    assert len(json.loads(output.with_suffix(".batch.json").read_text())["data"]) == 2
    assert [call.args[:2] for call in sdk.request.call_args_list] == [
        ("/api/v1/crawler/scrape/batch", "POST"),
        (endpoint, "GET"),
        (endpoint + "?skip=1", "GET"),
    ]
    unlocker.scrape.assert_called_once()


def test_resume_reuses_successful_repair_after_second_repair_fails(tmp_path):
    output = tmp_path / "documents.json"
    docs = fixtures()[:2]
    urls = [doc.metadata["url"] for doc in docs]
    failed = {
        "status": "completed",
        "data": [{"metadata": {"url": url, "statusCode": 403}} for url in urls],
    }
    sdk = Mock()
    sdk.request.side_effect = [{"id": "original-task"}, failed, failed]
    unlocker = Mock()
    unlocker.scrape.side_effect = [docs[0].text, OSError("Interrupted"), docs[1].text]
    reader = app.ScrapelessReader(
        client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk), universal=unlocker)
    )
    with patch.object(app, "ScrapelessReader", return_value=reader):
        with pytest.raises(OSError, match="Interrupted"):
            app.collect(output, urls)
        assert not output.exists()
        result = app.collect(output, urls, task_id="original-task")
    assert result["validation"] == "passed"
    assert [call.args[0].input["url"] for call in unlocker.scrape.call_args_list] == [
        urls[0],
        urls[1],
        urls[1],
    ]
    assert [call.args[1] for call in sdk.request.call_args_list] == ["POST", "GET", "GET"]


def test_duplicate_failed_pages_are_repaired_once_and_validated_before_checkpoint():
    doc = fixtures()[0]
    url = doc.metadata["url"]
    item = {"metadata": {"url": url, "statusCode": 403}}
    duplicate = {"metadata": {"url": url + "/", "statusCode": 403}}
    error = app.ScrapelessCrawlPageError([item, duplicate], [0, 1])
    reader = Mock()
    reader.load_data.return_value = [doc]
    save = Mock()
    repaired, repairs = app.recover_pages(reader, error, [url], checkpoint_repair=save)
    reader.load_data.assert_called_once()
    save.assert_called_once_with(app.normalized_url(url), [doc])
    assert repaired == [doc] and len(repairs) == 1


@pytest.mark.parametrize("status", [{}, "invalid", 403])
def test_failed_repair_is_not_checkpointed(status):
    source = fixtures()[0]
    url = source.metadata["url"]
    doc = Document(text=source.text, metadata={"url": url, "statusCode": status})
    error = app.ScrapelessCrawlPageError([{"metadata": {"url": url, "statusCode": 403}}], [0])
    reader = Mock()
    reader.load_data.return_value = [doc]
    save = Mock()
    with pytest.raises(ValueError, match="status"):
        app.recover_pages(reader, error, [url], checkpoint_repair=save)
    save.assert_not_called()


def test_challenge_page_with_success_status_is_rejected():
    original = fixtures()[0]
    url = original.metadata["url"]
    challenge = Document(text="# Just a moment...\n\n" + original.text, metadata={"url": url})
    with pytest.raises(ValueError, match="challenge page"):
        app.validate_documents([challenge], [url])


@pytest.mark.parametrize("second_command", ["collect", "discover"])
def test_concurrent_collect_locks_output_and_checkpoint(tmp_path, second_command):
    output = tmp_path / "documents.json"
    urls = [doc.metadata["url"] for doc in fixtures()]
    submitting, release = Event(), Event()
    reader = Mock()
    reader.load_data.return_value = fixtures()

    def submit(*args):
        submitting.set()
        assert release.wait(5)
        return {"id": "original-task"}

    request = reader.client.scraping_crawl.scrape.request
    request.side_effect = submit
    with patch.object(app, "ScrapelessReader", return_value=reader), ThreadPoolExecutor() as pool:
        first = pool.submit(app.collect, output, urls)
        try:
            assert submitting.wait(5)
            alias = tmp_path / "alias"
            alias.symlink_to(tmp_path, target_is_directory=True)
            with pytest.raises(ValueError, match="Another run"):
                if second_command == "collect":
                    app.collect(alias / output.name, urls)
                else:
                    app.discover(alias / output.with_suffix(".task.json").name)
        finally:
            release.set()
        assert first.result(timeout=5)["validation"] == "passed"
    request.assert_called_once()


def test_process_exit_releases_lock_and_resume_uses_saved_task(tmp_path):
    output = tmp_path / "documents.json"
    urls = [doc.metadata["url"] for doc in fixtures()]
    child = """
import importlib.util
import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("child_app", sys.argv[1])
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
reader = Mock()
reader.client.scraping_crawl.scrape.request.return_value = {"id": "interrupted-task"}

def blocked_poll(*args, **kwargs):
    print("polling", flush=True)
    time.sleep(30)

reader.load_data.side_effect = blocked_poll
app.ScrapelessReader = lambda **kwargs: reader
app.collect(Path(sys.argv[2]), json.loads(sys.argv[3]))
"""
    marker = tmp_path / "child.log"
    with (
        marker.open("w") as stream,
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                child,
                str(Path(app.__file__).resolve()),
                str(output),
                json.dumps(urls),
            ],
            stdout=stream,
        ) as process,
    ):
        try:
            deadline = time.monotonic() + 10
            while marker.read_text().strip() != "polling":
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            with patch.object(app, "ScrapelessReader") as reader:
                with pytest.raises(ValueError, match="Another run"):
                    app.collect(output, urls)
                reader.assert_not_called()
        finally:
            process.kill()
            process.wait(timeout=5)

    checkpoint = json.loads(output.with_suffix(".task.json").read_text())
    assert checkpoint["task_id"] == "interrupted-task"
    assert not output.exists()
    with patch.object(app, "ScrapelessReader") as factory:
        reader = factory.return_value
        reader.load_data.return_value = fixtures()
        assert app.collect(output, urls, task_id="interrupted-task")["validation"] == "passed"
        assert reader.load_data.call_args.kwargs["task_id"] == "interrupted-task"
        reader.client.scraping_crawl.scrape.request.assert_not_called()


@pytest.mark.parametrize(
    "cursor", ["https://other.example/api/v1/crawler/scrape/batch/task", "https://["]
)
def test_invalid_cursor_cannot_trigger_partial_repair(tmp_path, cursor):
    output = tmp_path / "documents.json"
    url = fixtures()[0].metadata["url"]
    sdk = Mock(base_url="https://api.scrapeless.com")
    sdk.request.side_effect = [
        {"id": "task"},
        {
            "status": "completed",
            "data": [{"metadata": {"url": url, "statusCode": 403}}],
            "next": cursor,
        },
    ]
    unlocker = Mock()
    reader = app.ScrapelessReader(
        client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk), universal=unlocker)
    )
    with patch.object(app, "ScrapelessReader", return_value=reader):
        with pytest.raises(RuntimeError) as error:
            app.collect(output, [url])
    assert error.value.task_id == "task"
    assert sdk.request.call_count == 2
    assert not output.exists()
    assert json.loads(output.with_suffix(".task.json").read_text())["task_id"] == "task"
    unlocker.scrape.assert_not_called()


def test_failed_batch_page_cannot_repair_an_unrequested_url():
    reader = Mock()
    error = app.ScrapelessCrawlPageError(
        [
            {"metadata": {"url": "https://example.com/expected", "statusCode": 403}},
            {"metadata": {"url": "https://example.net/unexpected", "statusCode": 403}},
        ],
        [0, 1],
    )
    with pytest.raises(ValueError, match="requested source"):
        app.recover_pages(reader, error, ["https://example.com/expected"])
    reader.load_data.assert_not_called()


@pytest.mark.parametrize("failure", ["transport", "timeout"])
def test_poll_failure_leaves_checkpoint_for_get_only_resume(tmp_path, failure):
    output = tmp_path / "documents.json"
    checkpoint = output.with_suffix(".task.json")
    url = fixtures()[0].metadata["url"]
    sdk = Mock()
    sdk.request.side_effect = [
        {"id": "existing-task"},
        OSError("Controlled GET failure") if failure == "transport" else {"status": "running"},
        {
            "status": "completed",
            "data": [{"markdown": fixtures()[0].text, "metadata": {"url": url}}],
        },
    ]
    reader = app.ScrapelessReader(
        client=SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=sdk))
    )
    with (
        patch.object(app, "ScrapelessReader", return_value=reader),
        patch.object(
            reader, "_pause", side_effect=app.ScrapelessTaskTimeout("batch_scrape", "existing-task")
        ),
    ):
        with pytest.raises((app.ScrapelessTaskError, app.ScrapelessTaskTimeout)) as error:
            app.collect(output, [url])
        assert error.value.task_id == "existing-task"
        saved = checkpoint.read_bytes()
        assert not output.exists()
        assert app.collect(output, [url], task_id="existing-task")["validation"] == "passed"
        assert checkpoint.read_bytes() == saved
    assert [call.args[1] for call in sdk.request.call_args_list] == ["POST", "GET", "GET"]


def test_cancelled_agent_keeps_trace_and_releases_output_locks(tmp_path):
    from llama_index.core.agent import workflow

    output = tmp_path / "report.json"
    handler = Mock(cancel_run=AsyncMock())

    async def events():
        raise asyncio.CancelledError
        yield

    handler.stream_events.side_effect = events
    with patch.object(workflow, "FunctionAgent") as agent:
        agent.return_value.run.return_value = handler
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(app.run_agent(app.build_retriever(fixtures()), "Example", Mock(), output))
        with pytest.raises(ValueError, match="exists"):
            asyncio.run(app.run_agent(app.build_retriever(fixtures()), "Example", Mock(), output))
    handler.cancel_run.assert_awaited_once()
    assert json.loads(output.read_bytes())["status"] == "cancelled"
    assert not output.with_suffix(".md").exists()
    assert not output.with_suffix(".html").exists()


def test_incomplete_corpus_resumes_same_task_and_preserves_previous_output(tmp_path):
    output = tmp_path / "documents.json"
    docs = fixtures()
    urls = [doc.metadata["url"] for doc in docs]
    reader = Mock()
    reader.client.scraping_crawl.scrape.request.return_value = {"id": "original-task"}
    reader.load_data.side_effect = [docs[:1], docs]

    with patch.object(app, "ScrapelessReader", return_value=reader):
        with pytest.raises(ValueError, match="Missing requested sources"):
            app.collect(output, urls)
        incomplete = output.read_bytes()
        result = app.collect(output, urls, task_id="original-task")

    assert result["validation"] == "passed"
    assert result["task_id"] == "original-task"
    assert (tmp_path / "documents.needs-review-1.json").read_bytes() == incomplete
    assert reader.load_data.call_args.kwargs["task_id"] == "original-task"
    reader.client.scraping_crawl.scrape.request.assert_called_once()


@pytest.mark.parametrize("task_id", [None, "other-task"])
def test_incomplete_corpus_rejects_new_or_mismatched_task(tmp_path, task_id):
    output = tmp_path / "documents.json"
    app.write_json(output.with_suffix(".task.json"), {"task_id": "original-task", "urls": app.URLS})
    app.write_json(
        output,
        {
            "schema_version": 1,
            "validation": "needs_review",
            "requested_urls": app.URLS,
            "task_id": "original-task",
            "documents": [],
        },
    )
    incomplete = output.read_bytes()

    with patch.object(app, "ScrapelessReader") as reader:
        with pytest.raises(ValueError, match="Resume the checkpoint"):
            app.collect(output, app.URLS, task_id=task_id)
        reader.assert_not_called()

    assert output.read_bytes() == incomplete
    assert not list(tmp_path.glob("*.needs-review-*.json"))


def test_validated_corpus_is_not_overwritten_on_resume(tmp_path):
    output = tmp_path / "documents.json"
    app.write_json(output.with_suffix(".task.json"), {"task_id": "original-task", "urls": app.URLS})
    app.write_json(
        output,
        {
            "schema_version": 1,
            "validation": "passed",
            "requested_urls": app.URLS,
            "task_id": "original-task",
            "documents": [],
        },
    )
    accepted = output.read_bytes()

    with patch.object(app, "ScrapelessReader") as reader:
        with pytest.raises(ValueError, match="Corpus already exists"):
            app.collect(output, app.URLS, task_id="original-task")
        reader.assert_not_called()

    assert output.read_bytes() == accepted


def test_serialization_preserves_context_exclusions(tmp_path):
    path = tmp_path / "corpus.json"
    docs = fixtures()
    app.write_json(
        path,
        {
            "schema_version": 1,
            "validation": "passed",
            "requested_urls": [d.metadata["url"] for d in docs],
            "documents": [d.to_dict() for d in docs],
        },
    )
    result = app.load_documents(path)
    assert "task_id" in result[0].excluded_llm_metadata_keys
    assert "controlled-task" not in result[0].get_content(metadata_mode="llm")


def test_unknown_source_and_uncited_answer_are_rejected():
    retrievers = app.build_retriever(fixtures())
    with pytest.raises(ValueError, match="Unknown source_id"):
        app.search_passages(retrievers, "tourism", "src-unknown")
    with pytest.raises(ValueError, match="valid citations"):
        app.validate_answer("Plausible unsupported report [src-12345678].", [], set())


@pytest.mark.parametrize(
    "content",
    [
        "[relative](../other)",
        "[encoded](&#47;&#47;outside.example/evidence)",
        "[inline][source]\n\n[source]: https://outside.example/evidence",
        "<https://outside.example/evidence>",
        "Source: https://outside.example/evidence.",
        "[unsafe](javascript:alert(1))",
        "![source](https://example.com/allowed)",
        "![data image](data:image/png;base64,AAAA)",
        'See <img src="//outside.example/inline.png">.',
        '<img src="//outside.example/block.png">',
    ],
)
def test_report_rejects_untrusted_destinations_and_html(content):
    with pytest.raises(ValueError, match="link|URL|image|HTML"):
        app.render_report(content, {"https://example.com/allowed"})


def test_report_preserves_retrieved_markdown_links_and_tables():
    url = "https://example.com/a_(b)?q=one&sort=two"
    answer = f"| Source |\n| --- |\n| [Evidence](<{url}>) |\n"
    html = app.render_report(answer, {url})
    assert '<a href="https://example.com/a_(b)?q=one&amp;sort=two">' in html
    assert "<table>" in html


def test_research_rejects_two_sources_before_starting_agent(tmp_path):
    output = tmp_path / "report.json"
    with patch.object(app, "research_tools") as tools:
        with pytest.raises(ValueError, match="at least three"):
            asyncio.run(
                app.run_agent(app.build_retriever(fixtures()[:2]), "Example", Mock(), output)
            )
        tools.assert_not_called()
    assert not output.exists()


def test_missing_model_settings_fail_before_network(monkeypatch):
    for key in ["LLM_MODEL", "LLM_API_KEY", "LLM_API_BASE_URL"]:
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(app.ModelConfigurationError, match="LLM_API_KEY"):
        app.configured_llm()


@pytest.mark.parametrize(
    "collision",
    [
        None,
        "suffix",
        "json",
        "md",
        "html",
        "symlink",
        "late-html",
        "late-json",
        "external-image",
        "external-link",
        "missing-search",
        "failed-search",
        "unscoped-search",
        "repeated-query",
        "four-sources",
        "eight-sources",
    ],
)
def test_multistep_agent_records_evidence_and_exports_report(tmp_path, collision):
    docs = fixtures()
    if collision in {"missing-search", "failed-search", "unscoped-search", "four-sources"}:
        docs.append(Document(text=docs[0].text, metadata={"url": "https://example.com/source-3"}))
    if collision == "eight-sources":
        docs.extend(
            Document(text=docs[0].text, metadata={"url": f"https://example.com/source-{i}"})
            for i in range(3, 8)
        )
    ids = [app.source_id(d.metadata["url"]) for d in docs]

    def responses(messages, **kwargs):
        if collision == "late-html":
            output.with_suffix(".html").touch(exist_ok=True)
        if collision == "late-json":
            output.write_text("Trace changed outside the Agent")
        returned = [m for m in messages if m.role == "tool"]
        count = len(returned)
        if count <= len(docs):
            name = "list_sources" if count == 0 else "search_evidence"
            arguments = (
                {}
                if count == 0
                else {
                    "query": (
                        "tourism evidence"
                        if collision == "repeated-query"
                        else f"tourism evidence question {count}"
                    ),
                    "source_id": (
                        ids[0]
                        if count == 4 and collision == "missing-search"
                        else "src-unknown"
                        if count == 4 and collision == "failed-search"
                        else ""
                        if count == 4 and collision == "unscoped-search"
                        else ids[count - 1]
                    ),
                }
            )
            return ChatMessage(
                role="assistant",
                blocks=[
                    ToolCallBlock(
                        tool_call_id=f"controlled-{count}",
                        tool_name=name,
                        tool_kwargs=arguments,
                    )
                ],
            )
        text = "# Controlled fixture report\n\nSynthetic research only.\n\n"
        for identity, doc in zip(ids[:3], docs[:3], strict=True):
            text += f"A controlled source was retrieved [{identity}]. {doc.metadata['url']}\n\n"
        if collision == "external-image":
            text += "![unexpected image](//outside.example/unretrieved.png)"
        if collision == "external-link":
            text += "[unexpected link](//outside.example/unretrieved)"
        return ChatMessage(role="assistant", content=text)

    llm = MockFunctionCallingLLM(response_generator=responses, is_chat_model=True)
    output = tmp_path / ("report.md" if collision == "suffix" else "report.json")
    retriever = app.build_retriever(docs)

    def run():
        return asyncio.run(
            app.run_agent(
                retriever, "Research synthetic tourism.", llm, output, mode="controlled_model"
            )
        )

    if collision == "symlink":
        target = tmp_path / "missing"
        output.with_suffix(".md").symlink_to(target)
        with pytest.raises(ValueError, match="exists"):
            run()
        assert output.with_suffix(".md").is_symlink()
        assert not target.exists()
        return
    if collision in {"suffix", "json", "md", "html"}:
        occupied = output.with_suffix("." + collision) if collision != "suffix" else None
        if occupied:
            occupied.write_text("Existing user file")
        with patch.object(app, "research_tools") as tools:
            with pytest.raises(ValueError, match=r"exists|\.json"):
                run()
        tools.assert_not_called()
        if occupied:
            assert occupied.read_text() == "Existing user file"
        return
    if collision == "late-html":
        with pytest.raises(FileExistsError):
            run()
        assert output.with_suffix(".html").read_bytes() == b""
        assert not output.with_suffix(".md").exists()
        assert json.loads(output.read_text())["status"] == "failed"
        return
    if collision == "late-json":
        with pytest.raises(ValueError, match="changed"):
            run()
        assert output.read_text() == "Trace changed outside the Agent"
        assert not output.with_suffix(".md").exists()
        assert not output.with_suffix(".html").exists()
        return
    if collision in {
        "external-image",
        "external-link",
        "missing-search",
        "failed-search",
        "unscoped-search",
        "repeated-query",
    }:
        with pytest.raises(ValueError, match="image|link|evidence search"):
            run()
        assert json.loads(output.read_text())["status"] == "failed"
        assert not output.with_suffix(".md").exists()
        assert not output.with_suffix(".html").exists()
        return
    result = run()
    assert result["mode"] == "controlled_model"
    assert result["status"] == "citation_ids_checked"
    assert len(result["sources"]) == 3
    assert len([e for e in result["trace"] if e["event"] == "tool_call"]) == len(docs) + 1
    assert output.with_suffix(".md").is_file()
    assert "Controlled fixture report" in output.with_suffix(".html").read_text()
