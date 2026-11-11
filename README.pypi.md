# Scrapeless Reader for LlamaIndex

Load completed Scrapeless results as LlamaIndex `Document` objects. The Reader uses the Scrapeless Python SDK and retains source information for downstream indexing and retrieval.

## Install

Use Python 3.10 or later:

```bash
python -m pip install llama-index-readers-scrapeless==0.1.0
```

Set `SCRAPELESS_API_KEY` to a key from your [Scrapeless dashboard](https://app.scrapeless.com/?utm_source=pypi&utm_medium=referral&utm_campaign=llamaindex-scrapeless&utm_content=package-description). On macOS or Linux:

```bash
export SCRAPELESS_API_KEY="YOUR_SCRAPELESS_API_KEY"
```

## Load a page

Save the following as `quickstart.py` in your working directory:

```python
from llama_index.readers.scrapeless import ScrapelessReader

reader = ScrapelessReader(timeout=120, request_timeout=30)
documents = reader.load_data(
    "scrape",
    params={
        "url": "https://example.com",
        "options": {"formats": ["markdown"], "onlyMainContent": True},
    },
)

for document in documents:
    print("Source:", document.metadata.get("url"))
    print(document.text[:300])
```

Run `python quickstart.py`. This sends a live Scrapeless request. Check that the returned text describes the example domain and that the source URL matches the requested page. An empty list means no usable documents were returned. Failed or malformed task results raise an error.

## Supported services

| Product | Reader service | Input |
| --- | --- | --- |
| Scraping API | `google_search`, `scraping` | Search input, or an actor and its input |
| Crawl | `scrape`, `batch_scrape`, `crawl` | Page URL or batch URL list, with optional Crawl settings |
| Web Unlocker | `web_unlocker` | Actor and request input |
| AI Scraper | `ai_scraper` | Actor and engine-specific input |

See the [Scrapeless Python SDK reference](https://docs.scrapeless.com/en/docs/developer-tools/sdks/python-sdk/?utm_source=pypi&utm_medium=referral&utm_campaign=llamaindex-scrapeless&utm_content=package-description) for product inputs. The Reader returns `list[Document]` across all seven routes. Choose your own LlamaIndex indexing, storage, and model configuration for downstream use.

On a polling timeout or a task result error, retain the original task ID when available. Polling timeout does not cancel the remote job. Inspect the original job before deciding whether to resume it; the Reader does not automatically submit a replacement.

The source distribution includes the repository examples, usage notes, and tests. Download the source archive from the project's PyPI files if you want to run those examples. The installed wheel is sufficient for the standalone quick start above.

For support, contact [support@scrapeless.com](mailto:support@scrapeless.com) with the version, reproduction steps, and a sanitized error message.
