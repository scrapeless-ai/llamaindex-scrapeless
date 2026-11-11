# Tourism and hotel-market research Agent

This optional application collects official tourism evidence and uses LlamaIndex tools to produce a cited research brief. The example focuses on London inbound tourism with explicitly labelled England/UK context. It is a bounded multi-source research workflow, not an exhaustive industry survey or a live hotel-rate comparison.

This optional application adds source discovery, task checkpoints, partial-batch recovery, local passage retrieval, model configuration, trace capture, and report export. It uses local JSON files and in-memory BM25 indexes; a database or embedding API is not required.

## Install the source and optional dependencies

Obtain the repository source or extract the source distribution. Use Python 3.10 or later; the example is tested with Python 3.13. From the source root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip install -r examples/tourism_research/requirements.txt
python -m pip check
```

The optional requirements select LlamaIndex Core 0.14.25, BM25 Retriever 0.8.0, OpenAI-compatible LLM adapter 0.8.1, and Markdown renderer 4.0.0.

Create a private `.env` in the repository root, using the actual settings for a model that supports OpenAI-compatible Chat Completions and tool calling:

```dotenv
SCRAPELESS_API_KEY=YOUR_SCRAPELESS_API_KEY
LLM_API_BASE_URL=https://YOUR_MODEL_PROVIDER/v1
LLM_API_KEY=YOUR_MODEL_API_KEY
LLM_MODEL=YOUR_TOOL_CALLING_MODEL
LLM_CONTEXT_WINDOW=64000
LLM_MAX_TOKENS=4000
```

Use your model's documented context limit. If its API accepts a reasoning-effort setting, `LLM_REASONING_EFFORT` can be supplied separately; otherwise leave it unset. The execution has a 300-second workflow budget and a 60-second per-model-request timeout. The Agent iteration limit is ten for the default run and up to twelve for larger source lists. Model calls and Scrapeless collection can incur charges.

## 1. Discover sources

```bash
python examples/tourism_research/app.py discover
```

This calls the Reader's `google_search` route and saves search snippets and URLs to `outputs/tourism/discovery.json`. Search results are discovery evidence, not the full pages. The collection list is a curated set of four first-party sources; it is not automatically selected by the model:

- VisitBritain: annual regional inbound visits and spending.
- VisitEngland: latest England hotel occupancy, ADR, and RevPAR.
- VisitEngland: England accommodation stock audit.
- Office for National Statistics: travel trends, definitions, and comparability notes.

The exact URLs are in `URLS` in `app.py`. A page named "latest" can change after the recorded execution. The corpus retains its collection time and source contents; a future run may produce different figures.

## 2. Collect and validate the pages

```bash
python examples/tourism_research/app.py collect
```

The application submits a batch through the official SDK, saves its task ID before polling, and lets `ScrapelessReader.load_data("batch_scrape", task_id=...)` wait for its result. This explicit submission ensures that the ID is retained even when a completed batch contains a page error.

For a mixed-error batch, the application uses the complete page list already returned to the Reader. It keeps usable batch pages and calls `web_unlocker` for failed URLs in the original request. Each verified repair is saved in the task checkpoint immediately, so a later repair failure can be resumed without fetching those verified pages again.

Inspect `outputs/tourism/documents.json` for exactly the requested source URLs, usable text, page status when provided, and `validation="passed"`. Missing or extra URLs, malformed HTTP status, and recognized challenge pages prevent acceptance. A validated corpus is kept unchanged. A written corpus that fails validation carries `validation="needs_review"`.

If local waiting fails or source coverage is incomplete, use the task ID in `outputs/tourism/documents.task.json` and resume with the original URLs and output:

```bash
python examples/tourism_research/app.py collect --task-id YOUR_EXISTING_TASK_ID
```

When a resumed request returns a new result, any existing `needs_review` corpus for that checkpoint is saved as `documents.needs-review-N.json` before writing the new result. The checkpoint also retains validated page repairs for that task. If the original task still lacks a source, keep the result and inspect the original task before choosing any new submission.

Each output and its companion files are locked while collection runs. A second command using any of those paths stops before it can submit another batch. If a process exits after saving the checkpoint, resume with that task ID.

For custom URLs, provide the same `--urls URL_1 URL_2` values on resumption. `--repair-cache` is an optional path to previously saved live Web Unlocker Documents; using it is replaying retained evidence, not a new collection. Never pass an unverified or synthetic cache as a live result.

## 3. Run the research Agent

```bash
python examples/tourism_research/app.py research
```

`FunctionAgent` first calls `list_sources`, then uses `search_evidence` with a separate question and an explicit `source_id` for each source. Research requires at least three collected sources. Each source has a BM25 passage index. Tool results include a bounded opening-page overview as well as ranked passages, so that headline figures and date context are available when a long query ranks methodology text highly. Scores rank lexical matches within a source.

The source catalogue extracts publication dates from explicit page labels without inventing missing dates. Retrieved text remains untrusted reference data. The prompt requires geography, observation period, release date, units, and statistical limitations to accompany the report's claims.

Outputs:

| File | Contents |
| --- | --- |
| `outputs/tourism/report.json` | Question, original model answer, actual tool calls/results, cited source identities, and validation state |
| `outputs/tourism/report.md` | Original generated report after citation/trace checks pass |
| `outputs/tourism/report.html` | Readable export of the same report |

Before exporting Markdown and HTML, the application verifies a successful, source-specific evidence search for every source, at least three cited source IDs, and links to retrieved source URLs. Markdown images and raw HTML fail validation. Independently review numerical claims, geographical scope, dates, definitions, and inferences against the saved passages.

Use a `.json` path for `--output`. Choose a name with free `.json`, `.md`, and `.html` paths. Each run reserves those paths while it executes. If the Agent fails or is cancelled, inspect the saved JSON trace.

The default question separates London inbound visits/spending from England hotel metrics. ADR is an average hotel-rate statistic, not a bookable room quote. Inbound visits are not unique people, and total visitor spending is not hotel revenue. Missing London-specific room-price bands or hotel performance should remain explicit evidence gaps.

## Reuse the corpus

After inspecting a completed report, choose another output path for a new model run:

```bash
python examples/tourism_research/app.py research --output outputs/tourism/followup.json --question "Which methodological limitations should an analyst preserve when comparing these tourism sources? Cite the collected evidence."
```

The same evidence requirements apply, including multiple source-specific queries and citations. Retrieval and model iterations reuse the saved corpus and do not trigger another Scrapeless collection. Keep failed or superseded runs separately rather than rewriting their history.

## Controlled application checks

```bash
python -m pip install -e '.[dev]'
pytest tests examples/tourism_research/test_app.py
ruff check .
ruff format --check .
```

Application checks use synthetic documents and a synthetic model response generator. Real data collection and model acceptance are recorded separately. No live credentials are needed for these checks, and CI must not run the billable collection/research commands.
