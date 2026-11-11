"""Inspect source-linked retrieval with a local token-count embedding."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from llama_index.core import Document, VectorStoreIndex
from llama_index.core.embeddings import BaseEmbedding


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class TokenCountEmbedding(BaseEmbedding):
    """A lexical baseline for this example, not a semantic embedding model."""

    vocabulary: dict[str, int]

    def _get_text_embedding(self, text: str) -> list[float]:
        counts = Counter(tokens(text))
        return [float(counts.get(word, 0)) for word in self.vocabulary]

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)


def retrieve(payload: dict, query: str, top_k: int = 3) -> list[dict]:
    docs = [Document(**item) for item in payload["documents"]]
    if not docs:
        return []
    words = sorted({word for doc in docs for word in tokens(doc.text)})
    if not words or not set(tokens(query)).intersection(words):
        return []
    embedding = TokenCountEmbedding(vocabulary={word: i for i, word in enumerate(words)})
    index = VectorStoreIndex.from_documents(docs, embed_model=embedding)
    nodes = index.as_retriever(similarity_top_k=top_k).retrieve(query)
    return [
        {
            "text": node.node.get_content(),
            "url": node.node.metadata.get("url"),
            "score": node.score,
        }
        for node in nodes
        if node.score is not None and node.score > 0
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--query", required=True)
    parser.add_argument("--top-k", type=int, default=3)
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    payload = json.loads(args.input.read_text())
    result = {
        "mode": payload["mode"],
        "query": args.query,
        "matches": retrieve(payload, args.query, args.top_k),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
