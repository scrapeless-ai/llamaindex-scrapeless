"""Validate package metadata and fail closed before a publication is requested."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

PACKAGE = "llama-index-readers-scrapeless"
DESTINATIONS = ("validate", "testpypi", "pypi")


def check_release(
    project: dict[str, Any],
    *,
    destination: str = "validate",
    expected_version: str = "",
    enabled: str = "",
    repository: str = "",
    release_repository: str = "",
    ref: str = "",
) -> str:
    """Validate without uploading, creating tags, or changing external state."""
    if destination not in DESTINATIONS:
        raise ValueError("Unknown release destination.")
    if project.get("name") != PACKAGE:
        raise ValueError("The distribution name does not match this integration.")
    version = str(Version(project["version"]))
    if expected_version and expected_version != version:
        raise ValueError("Requested version must match pyproject.toml.")
    if destination == "validate":
        return version
    if enabled != "true":
        raise ValueError("Publishing is disabled: RELEASE_PUBLISH_ENABLED must equal true.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", release_repository):
        raise ValueError("RELEASE_REPOSITORY must identify the approved owner/repository.")
    if repository != release_repository:
        raise ValueError("Publication must run from the approved repository.")
    if ref != f"refs/tags/v{version}":
        raise ValueError("Publication must run from the matching vVERSION tag.")
    urls = project.get("urls", {})
    if urls.get("Repository", "").rstrip("/") != f"https://github.com/{repository}":
        raise ValueError("Set project.urls.Repository to the approved GitHub repository.")
    return version


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", choices=DESTINATIONS, default="validate")
    parser.add_argument("--expected-version", default="")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    try:
        version = check_release(
            project,
            destination=args.destination,
            expected_version=args.expected_version,
            enabled=os.environ.get("RELEASE_PUBLISH_ENABLED", ""),
            repository=os.environ.get("GITHUB_REPOSITORY", ""),
            release_repository=os.environ.get("RELEASE_REPOSITORY", ""),
            ref=os.environ.get("GITHUB_REF", ""),
        )
    except ValueError as error:
        parser.exit(1, f"Release check failed: {error}\n")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(f"version={version}\n")
    print(json.dumps({"destination": args.destination, "version": version, "uploaded": False}))


if __name__ == "__main__":
    main()
