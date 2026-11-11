"""Verify that the documented offline path produces useful source retrieval."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from llama_index.core.schema import Document, MetadataMode


def test_offline_collection_and_llamaindex_retrieval(tmp_path):
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "documents.json"
    environment = dict(os.environ)
    environment.pop("SCRAPELESS_API_KEY", None)
    subprocess.run(
        [
            sys.executable,
            str(root / "examples/collect.py"),
            "--offline",
            "--output",
            str(target),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    payload = json.loads(target.read_text())
    assert payload["mode"] == "offline_fixture"
    assert len(payload["documents"]) == 3
    restored = Document(**payload["documents"][0])
    assert restored.metadata["task_id"] == "offline-fixture"
    for mode in (MetadataMode.EMBED, MetadataMode.LLM):
        assert "offline-fixture" not in restored.get_content(metadata_mode=mode)
    spec = importlib.util.spec_from_file_location("retrieve_example", root / "examples/retrieve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    matches = module.retrieve(payload, "refund window")
    assert matches[0]["url"] == "https://example.com/refunds"
    assert "30 days" in matches[0]["text"]
    assert module.retrieve(payload, "unknownterm") == []
