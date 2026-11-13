"""Inspect distribution contents and run offline examples in isolated installations."""

from __future__ import annotations

import argparse
import email
import json
import os
import subprocess
import tarfile
import tempfile
import venv
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from packaging.utils import canonicalize_name

PACKAGE = "llamaindex-scrapeless"
BLOCKED = {"internal", "Blog", "Documentation", "ChineseReview", "Review", ".git", "logs"}


class DescriptionLinks(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attr = {"a": "href", "img": "src"}.get(tag)
        if attr is None:
            return
        reference = dict(attrs).get(attr)
        if not reference:
            return
        parsed = urlsplit(reference)
        if parsed.scheme in {"https", "http"} and parsed.netloc:
            return
        if tag == "a" and (reference.startswith("#") or parsed.scheme == "mailto"):
            return
        raise ValueError("PyPI description links and images must use public absolute URLs.")


def check_description(description: str) -> None:
    """Reject repository-relative assets in the package's long description."""
    renderer = MarkdownIt("commonmark", {"html": True}).enable("table")
    parser = DescriptionLinks()
    parser.feed(renderer.render(description))
    parser.close()


def check_members(names: list[str]) -> None:
    for name in names:
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts:
            raise ValueError("An archive member escapes its extraction directory.")
        if BLOCKED.intersection(parts) or "__pycache__" in parts:
            raise ValueError("A distribution contains excluded delivery/runtime files.")
        if any(part.startswith(".env") and part != ".env.example" for part in parts):
            raise ValueError("A distribution contains private environment configuration.")
        if name.endswith((".pyc", ".pem", ".key")):
            raise ValueError("A distribution contains an excluded runtime or credential file.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheels = list(args.dist.glob("*.whl"))
    sources = list(args.dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        parser.error("Expected exactly one wheel and one source distribution in --dist.")
    wheel, sdist = wheels[0].resolve(), sources[0].resolve()
    with zipfile.ZipFile(wheel) as archive:
        check_members(archive.namelist())
        metadata_names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ValueError("Expected one wheel METADATA file.")
        metadata = email.message_from_bytes(archive.read(metadata_names[0]))
        if canonicalize_name(metadata["Name"]) != PACKAGE:
            raise ValueError("Unexpected wheel project name.")
        check_description(metadata.get_payload())
        if not any("LICENSE" in name for name in archive.namelist()):
            raise ValueError("The wheel is missing its license.")
    environment = dict(os.environ)
    for name in ("SCRAPELESS_API_KEY", "OPENAI_API_KEY", "PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    results = []
    with tempfile.TemporaryDirectory(prefix="scrapeless-release-") as directory:
        work = Path(directory)
        with tarfile.open(sdist) as archive:
            members = archive.getmembers()
            check_members([m.name for m in members])
            if any(not (m.isfile() or m.isdir()) for m in members):
                raise ValueError("Source archives must not contain links or special files.")
            for member in members:
                target = work / "source" / member.name
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError("Could not read source archive member.")
                    target.write_bytes(stream.read())
        projects = list((work / "source").iterdir())
        if len(projects) != 1:
            raise ValueError("Expected one source project directory.")
        project = projects[0]
        for required in (
            "LICENSE",
            "README.md",
            "README.pypi.md",
            "pyproject.toml",
            "docs/validation.md",
        ):
            if not (project / required).is_file():
                raise ValueError(f"Missing source distribution file: {required}")
        source_metadata = email.message_from_bytes((project / "PKG-INFO").read_bytes())
        for field in ("Name", "Version", "Requires-Python", "Description-Content-Type"):
            if source_metadata[field] != metadata[field]:
                raise ValueError(f"Wheel and source distribution disagree on {field}.")
        if source_metadata.get_payload() != metadata.get_payload():
            raise ValueError("Wheel and source distribution descriptions differ.")
        for label, artifact in (("wheel", wheel), ("sdist", sdist)):
            prefix = work / label
            venv.create(prefix, with_pip=True, symlinks=os.name != "nt")
            python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

            def run(*arguments: str, cwd: Path = project, executable: Path = python) -> str:
                completed = subprocess.run(
                    [str(executable), *arguments],
                    cwd=cwd,
                    env=environment,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=240,
                )
                return completed.stdout

            print(f"Checking isolated {label} installation", flush=True)
            run("-m", "pip", "install", "--quiet", str(artifact), cwd=work)
            imported = Path(
                run("-c", "import llamaindex_scrapeless as r; print(r.__file__)", cwd=work).strip()
            ).resolve()
            if not imported.is_relative_to(prefix.resolve()):
                raise ValueError("Import came from outside the isolated installation.")
            output = work / f"{label}-documents.json"
            run("examples/collect.py", "--offline", "--output", str(output))
            saved = json.loads(output.read_text())
            if saved["mode"] != "offline_fixture" or len(saved["documents"]) != 3:
                raise ValueError("Offline collection did not produce its three fixture Documents.")
            if not all(
                "task_id" in doc["excluded_embed_metadata_keys"]
                and "task_id" in doc["excluded_llm_metadata_keys"]
                for doc in saved["documents"]
            ):
                raise ValueError("Saved Documents lost metadata exclusion lists.")
            retrieved = json.loads(
                run("examples/retrieve.py", "--input", str(output), "--query", "refund window")
            )
            match = retrieved["matches"][0]
            if match["url"] != "https://example.com/refunds" or "30 days" not in match["text"]:
                raise ValueError("Retrieval did not find the expected source and passage.")
            dependency_check = run("-m", "pip", "check").strip()
            results.append(
                {"artifact": label, "offline_retrieval": "passed", "dependencies": dependency_check}
            )
    print(json.dumps({"package": PACKAGE, "version": metadata["Version"], "checks": results}))


if __name__ == "__main__":
    main()
