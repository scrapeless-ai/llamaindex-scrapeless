# CI and release operations

The workflows belong at the root of the integration repository. Start from the reviewed repository archive in an independent directory. Verify that `git rev-parse --show-toplevel` resolves to that source root before staging files. Use the selected repository contents, not the containing delivery workspace. Standalone Doc/Blog drafts, review-only materials, local previews, and internal diagnostics are excluded from the repository and distributions.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests, manual requests, and calls from the release workflow. It performs:

1. Ruff lint and formatting checks, plus checksum-verified actionlint workflow validation.
2. Offline tests and dependency checks on Python 3.10, 3.11, 3.12, 3.13, and 3.14.
3. An explicit tourism application test job on Python 3.13, with the example's optional requirements and controlled model/service inputs.
4. Distribution builds and `twine check --strict` after lint and every test job pass.
5. Fresh wheel and source-distribution installations in separate virtual environments, followed by offline collection and source-linked retrieval. Package metadata must agree across both formats, and the PyPI description must not contain repository-relative resources.
6. Storage of the checked wheel and source archive as the `python-distributions` workflow artifact for seven days.

CI uses a read-only repository token and does not load Scrapeless credentials. The controlled tests mock service responses, and the example smoke checks use synthetic input. None of these jobs makes live Scrapeless API calls.

For local checks, install the development dependencies and run:

```bash
python -m pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest --cov=llama_index.readers.scrapeless --cov-report=term-missing
python -m pip check
python scripts/release_checks.py
python -m build
python -m twine check --strict dist/*
python scripts/verify_dist.py --dist dist
```

Start with an empty `dist/` directory. The distribution verifier requires exactly one wheel and one source archive and checks for private/runtime files, archive traversal, installed import paths, preserved metadata exclusions, and an expected retrieval result. It installs dependencies from the configured package index but does not call paid services.

## Manual release workflow

`.github/workflows/release.yml` only starts through `workflow_dispatch`. Its default `destination` is `validate`, which reruns CI and builds review artifacts without publishing. An optional `version` input must equal the version in `pyproject.toml`.

| Destination | Result |
| --- | --- |
| `validate` | Run checks and store artifacts; no registry upload |
| `testpypi` | Publish checked artifacts only after all publication gates pass |
| `pypi` | Publish checked artifacts only after all publication gates pass |

There is no automatic tag-push or GitHub Release publication trigger. The workflow does not create tags or GitHub Releases. Third-party Actions are pinned to full commit SHAs.

## Publication gates

Registry publication requires all of the following:

- Repository variable `RELEASE_PUBLISH_ENABLED` equals the exact string `true`. Leave it absent or `false` during review.
- Repository variable `RELEASE_REPOSITORY` equals the approved GitHub `owner/repository`, and the workflow is running in that repository.
- The workflow runs on `refs/tags/vVERSION`, matching `project.version` in `pyproject.toml`.
- `project.urls.Repository` is set to the actual approved `https://github.com/owner/repository` URL. The preflight rejects a missing or mismatched URL.
- CI succeeds and supplies the checked artifacts from the same workflow run.
- The selected GitHub Environment (`testpypi` or `pypi`) permits the deployment, and the registry accepts the configured Trusted Publisher identity.

The preflight job fails on an unconfigured or mismatched publication request; it does not silently report a skipped upload as successful. Validation mode works before the publishing infrastructure is configured.

Only the registry jobs receive `id-token: write`. They download the checked artifacts without checking out or executing repository scripts. Publishing uses OIDC Trusted Publishing, without storing a PyPI API token in this repository.

## One-time external setup, after review

1. Verify that `project.urls.Repository` points to the approved repository at `https://github.com/scraperapigather/llama-index-readers-scrapeless`. Add the integration documentation URL when the page is available.
2. Create GitHub Environments named `testpypi` and `pypi`. For a private repository, first check the plan and administrator permissions for Environments and required reviewers. Keep publication disabled until the intended approval process and tag restrictions can be applied. Environment protection is a GitHub setting, not a rule created by these YAML files.
3. Add a Trusted Publisher separately on TestPyPI and PyPI using the approved owner, repository, workflow filename `release.yml`, and matching environment name. For a new package, use the registry's pending-publisher flow. Package name availability and ownership must be confirmed there.
4. Set `RELEASE_REPOSITORY` to that repository. Keep `RELEASE_PUBLISH_ENABLED` disabled until publication is explicitly approved.
5. After approval, create the reviewed version tag and run `validate` at that tag. Verify hosted checks and artifacts before explicitly selecting a publishing destination.

A successful local check does not establish that GitHub-hosted CI has run, that Environment protection exists, or that a Trusted Publisher is configured. Publishing to TestPyPI is also an external publication and is not part of validation mode. The public registry description comes from `README.pypi.md`; keep it usable without private repository access and verify the actual rendered page after an authorized upload.

## References

- [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [GitHub Environment availability](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [Publishing with a PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [Adding a PyPI Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
