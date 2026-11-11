"""Save mapped Documents; offline fixtures are labeled and never call the API."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from llama_index.readers.scrapeless import ScrapelessReader


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--service",
        default="scrape",
        choices=[
            "scrape",
            "batch_scrape",
            "crawl",
            "google_search",
            "scraping",
            "web_unlocker",
            "ai_scraper",
        ],
    )
    parser.add_argument("--url", default="https://example.com")
    parser.add_argument("--params", type=Path, help="JSON file with service-specific parameters")
    parser.add_argument("--task-id", help="Resume an existing job")
    parser.add_argument("--key-file", type=Path, help="Private file containing only the API key")
    parser.add_argument("--offline", action="store_true", help="Use synthetic example documents")
    parser.add_argument("--output", type=Path, default=Path("outputs/documents.json"))
    args = parser.parse_args()
    if args.offline:
        if args.task_id or args.params or args.key_file or args.service != "scrape":
            parser.error("--offline cannot be combined with service, params, task, or key options")
        fixture = json.loads((Path(__file__).parent / "fixtures/pages.json").read_text())

        # Substitute only the SDK transport; exercise the normal batch Reader path.
        def fixture_request(endpoint, method, *args):
            if method == "POST":
                return {"id": "offline-fixture"}
            return {"status": "completed", "data": fixture}

        transport = SimpleNamespace(request=fixture_request)
        client = SimpleNamespace(scraping_crawl=SimpleNamespace(scrape=transport))
        docs = ScrapelessReader(client=client).load_data(
            "batch_scrape", params={"urls": [page["metadata"]["sourceURL"] for page in fixture]}
        )
        mode = "offline_fixture"
    else:
        key = args.key_file.read_text().strip() if args.key_file else None
        if args.key_file and not key:
            parser.error("The private key file is empty")
        params = json.loads(args.params.read_text()) if args.params else None
        if params is None:
            if args.service in {"scrape", "crawl"}:
                params = {"url": args.url}
            elif args.task_id is None:
                parser.error("This service requires --params")
        docs = ScrapelessReader(api_key=key).load_data(
            args.service, params=params, task_id=args.task_id
        )
        mode = "sdk_api"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "documents": [
            {
                "text": doc.text,
                "metadata": doc.metadata,
                "excluded_embed_metadata_keys": doc.excluded_embed_metadata_keys,
                "excluded_llm_metadata_keys": doc.excluded_llm_metadata_keys,
            }
            for doc in docs
        ],
    }
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved {len(docs)} Documents ({mode}) to {args.output}")


if __name__ == "__main__":
    main()
