# Changelog

## 0.1.0 (unreleased)

- Add an official-SDK-backed LlamaIndex Reader for single-page scraping, batch scraping, Crawl, Google Search, Scraping API, Web Unlocker, and AI Scraper.
- Check task completion, bound polling, retain task IDs for resumption, and read compatible result pagination.
- Support custom record selection and Document mapping, with source/reference metadata.
- Preserve embedding and LLM metadata exclusions in the collection JSON example.
- Provide offline collection and source-linked retrieval examples.
- Add multi-version CI, distribution verification, and manually gated Trusted Publishing configuration.
- Preserve the original task ID when a completed response is rejected.
- Keep non-string structured answer fields instead of dropping the record.
- Resume an incomplete tourism collection from its original task and preserve the earlier output.
- Run controlled tourism application tests in CI before building distributions.
- Use a dedicated PyPI description with a standalone installation example and public links.
- Retain task context on malformed records and cursors, and recover failed sources from any page of a completed batch.
- Lock shared tourism output paths and protect existing collection and report files from overwrites.
- Save validated page repairs in the task checkpoint so resumption can skip them.
- Enforce the requested source scope and validate report links before exporting Markdown and HTML.
