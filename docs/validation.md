# Validation scope

This is a local release candidate, not a published package or an accepted upstream listing. The accepted integration scope is the seven documented data-service routes below, with representative successful live SDK checks. The live service results below are retained from the Reader baseline. The release review uses controlled regressions for the task-context, structured-mapping, application recovery, and package-description changes.

Reader acceptance excludes storage services, snapshot APIs, vector databases, and generative/Agent capabilities. The separate tourism application has its own controlled CI job. The collector's optional JSON output and local retrieval example remain reproducible usage examples. Acceptance covers the Reader, mapping/error behavior, source installation, package distributions, and local workflow validation; hosted CI and registry publication remain separate.

## Retained Reader baseline

Before the release review changes, the Reader-only source was tested on November 10, 2026. Python 3.10-3.14 each passed all 130 controlled tests. A separate fresh environment followed the README installation and example workflow, and executed its Python snippets against the installed SDK with controlled HTTP responses. Fresh wheel and source-archive installations, dependency checks, workflow syntax, and disabled-publication checks passed locally.

Fresh representative live requests covered all seven routes: one page, a two-URL batch, a two-page linked crawl, organic search, structured scraping with the Google Search actor, Web Unlocker, and ChatGPT through AI Scraper. The other seven AI engines retain the earlier representative evidence described below; they were not called again in this final acceptance.

Two initial observations were resolved without changing Reader runtime code. With main-content extraction, the example page's title existed in metadata but was absent from its body; the README now checks the actual documentation-example content and its source rather than requiring a heading in the body. Direct SDK reads of the same task and Reader text matched exactly. An initial linked-site crawl completed with an empty raw API data list, which the Reader correctly preserved as `[]`; a separate public-site crawl returned two completed pages with distinct source URLs. Resuming the existing single-page task also passed source-linked retrieval without another submission.

## Baseline environment and local checks

- Scrapeless Python SDK: 1.3.0.
- LlamaIndex Core: 0.14.25.
- Executed runtimes: Python 3.10.22, 3.11.18, 3.12.16, 3.13.11, and 3.14.9.
- Controlled tests: 130 passed on each of the five runtimes, including 21 release-gate/archive-boundary cases. Reader package statement coverage remains 95.5%.
- Ruff lint, formatting, and actionlint workflow syntax checks pass locally.
- CI and manual release workflows are configured; GitHub-hosted runs have not been executed because no repository changes were pushed.
- Release validation defaults to no upload. Registry publication requires explicit external configuration and approval; see [release operations](releasing.md).
- The wheel and source distribution build; a fresh wheel installation and dependency check pass.
- The extracted source distribution's offline collection and LlamaIndex retrieval commands pass.

Controlled tests use the installed SDK with mocked HTTP responses. They cover null-option omission, service dispatch, completion states, malformed/failed responses, empty results, bounded polling, task resumption, result pagination, cursor restrictions, source/reference mapping, async thread delegation, and the offline indexing path. The offline example substitutes the SDK transport and exercises the normal batch Reader path.

## Representative live SDK checks

These results are from the retained live checks. The baseline README acceptance exercised all seven routes with real SDK/API calls; it used ChatGPT for the AI Scraper example. The eight-engine checks below are earlier representative evidence. The release review does not repeat paid calls solely to validate packaging or controlled regressions.

| Service route | Live input scope | Observed result | Remaining limit |
| --- | --- | --- | --- |
| `scrape` | One reserved example URL; Markdown | One nonempty Document with source metadata | Other page types/options not covered |
| `batch_scrape` | Two URLs with different content | Two Documents with distinct source URLs | Large batches and live result pagination not covered |
| `crawl` | Linked crawl; two-page limit; depth 1 | Two Documents with distinct source URLs | Large crawls and live result pagination not covered |
| `google_search` | One English query scoped to the reserved example domain | Nine organic-result Documents with snippets and source links | Other verticals/markets not live tested |
| `scraping` | Google Search actor through the generic route | One structured JSON Document | Amazon, TikTok, and other actor-specific schemas not live tested |
| `web_unlocker` | One URL; GET; redirects disabled | JSON-wrapped HTML mapped to nonempty text | Direct non-JSON transport and other actors not covered |
| `ai_scraper` | Representative requests to all eight documented engines | Completed answers mapped to Documents; nonempty references retained for Perplexity, Copilot, and Alexa | Every market, mode, and input combination is not established |

The AI engines checked were ChatGPT, Perplexity, Microsoft Copilot, Gemini, Grok, Amazon Alexa, Google AI Overview, and Google AI Mode. Copilot passed with US/search after earlier upstream task failures. Google AI Mode passed on a separate retry after an initial TLS error. Live checks used a 300-second polling budget where needed; response latency and upstream failures remain variable. The Reader never automatically resubmits a failed task.

Browser actions, Profiles, Extensions, Proxies, and standalone CAPTCHA tools are not exposed as LlamaIndex tools by this package. Supported service inputs follow each product's documented schema; successful representative requests do not establish coverage of every SDK operation or actor.

## Fresh README acceptance

The repository was copied into an isolated directory with no existing virtual environment. The README installation commands created a fresh Python 3.10.22 environment and installed the package using `python -m pip install -e .`. Dependency checks, all seven service examples, real collection/retrieval, no-key offline collection/retrieval, the development installation, tests, Ruff checks, and package builds passed.

Three issues were fixed during the walkthrough:

- The batch example used placeholder page paths that produced page errors. It now uses two existing pages with different content and returned two source-linked Documents.
- Saving only Document text and metadata lost the embedding/LLM exclusion lists. Collection JSON now preserves both lists; the offline regression checks that the task ID stays outside the rendered embedding and LLM context after reloading.
- Some search results returned a relative `link` alongside an absolute `redirect_link`. The default mapper now uses the absolute redirect in this case, while preserving an existing absolute source link. The fresh search check returned nine Documents with absolute URLs.

One historical batch task returned fewer pages than its input list. Direct official-SDK comparison confirmed that its raw API response already contained one page and the Reader preserved that result. Three fresh SDK/Reader pairs, including the same URLs and Markdown parameters, returned two pages in every task with matching request bodies and source/content hashes. The historical server-side cause remains unresolved; applications requiring every input URL should check returned source coverage.

## Fixes established by live checks

SDK 1.3.0's Crawl convenience methods include unset dataclass options as null. The API rejected the minimal requests with validation errors for those null values. The adapter validates field names with SDK dataclasses and submits compacted payloads through the SDK request layer. Single-page, batch, and crawl submissions then completed successfully.

AI answer mapping recognizes common fields including ChatGPT `result_text`, Grok `full_response`, and Alexa `md_text`. Reference fields, including Perplexity `web_results`, Copilot `citations`/`links`, and Alexa `sources`/`references`, are retained as metadata. Empty answers do not index accompanying ads. Custom selectors and mappers remain available for the seven supported service routes.

## Retrieval and publication boundaries

The no-key example uses synthetic documents labeled `offline_fixture`. It builds a real LlamaIndex index with local token-count embeddings and retrieves a source-linked refund passage. It does not generate an LLM answer or benchmark semantic retrieval quality.

Representative completed Crawl results were also resumed without new submissions and checked for expected page content and source-linked retrieval. Live result bodies and credentials are not included in this repository.

Publication to GitHub/PyPI, hosted CI, actual website rendering, and official directory acceptance are separate steps. Extra actor support must be validated against its actual schema before being advertised.
