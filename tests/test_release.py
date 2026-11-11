"""Exercise publication gates and distribution boundary checks without uploads."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_release = load_script("release_checks").check_release
verify_dist = load_script("verify_dist")
check_members = verify_dist.check_members
PROJECT = {
    "name": "llama-index-readers-scrapeless",
    "version": "0.1.0",
    "urls": {"Repository": "https://github.com/example/integration"},
}
APPROVED = {
    "enabled": "true",
    "repository": "example/integration",
    "release_repository": "example/integration",
    "ref": "refs/tags/v0.1.0",
}


def test_validation_does_not_require_publication_setup():
    assert check_release({"name": PROJECT["name"], "version": "0.1.0"}) == "0.1.0"


@pytest.mark.parametrize("enabled", ["", "false", "True"])
def test_publication_is_disabled_without_explicit_switch(enabled):
    with pytest.raises(ValueError, match="Publishing is disabled"):
        check_release(PROJECT, destination="pypi", **{**APPROVED, "enabled": enabled})


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "other/integration"},
        {"release_repository": ""},
        {"ref": "refs/heads/main"},
        {"ref": "refs/tags/v0.2.0"},
        {"expected_version": "0.2.0"},
    ],
)
def test_publication_rejects_wrong_identity_or_version(change):
    with pytest.raises(ValueError):
        check_release(PROJECT, destination="pypi", **{**APPROVED, **change})


def test_publication_requires_the_actual_project_repository_url():
    with pytest.raises(ValueError, match="project.urls.Repository"):
        check_release({**PROJECT, "urls": {}}, destination="pypi", **APPROVED)


@pytest.mark.parametrize("destination", ["testpypi", "pypi"])
def test_approved_context_passes_validation_without_uploading(destination):
    assert check_release(PROJECT, destination=destination, **APPROVED) == "0.1.0"


def test_unknown_destination_is_rejected():
    with pytest.raises(ValueError, match="Unknown release destination"):
        check_release(PROJECT, destination="other")


def test_wrong_package_name_is_rejected():
    with pytest.raises(ValueError, match="distribution name"):
        check_release({**PROJECT, "name": "other-project"})


@pytest.mark.parametrize(
    "member",
    [
        "../outside",
        "/absolute",
        "project/.env",
        "project/Review/index.html",
        "project/private.pem",
        "project/__pycache__/module.pyc",
    ],
)
def test_distribution_rejects_private_and_escaping_members(member):
    with pytest.raises(ValueError):
        check_members([member])


def test_distribution_accepts_public_source_and_placeholder_environment():
    check_members(["project/README.md", "project/.env.example", "project/scripts/verify_dist.py"])


@pytest.mark.parametrize(
    "description",
    [
        "[Release guide](docs/releasing.md)",
        "![Banner](assets/banner.png)",
        '<img src="assets/banner.png" alt="Banner">',
    ],
)
def test_package_description_rejects_repository_relative_resources(description):
    with pytest.raises(ValueError, match="public absolute URLs"):
        verify_dist.check_description(description)


def test_package_description_accepts_independent_public_navigation():
    verify_dist.check_description(
        "[SDK](https://example.com/docs) [Support](mailto:support@example.com) "
        "![Brand](https://example.com/banner.png)"
    )
