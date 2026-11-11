"""Load Scrapeless SDK results into LlamaIndex Documents."""

from .base import ScrapelessReader
from .errors import ScrapelessReaderError, ScrapelessTaskError, ScrapelessTaskTimeout

__all__ = [
    "ScrapelessReader",
    "ScrapelessReaderError",
    "ScrapelessTaskError",
    "ScrapelessTaskTimeout",
]
