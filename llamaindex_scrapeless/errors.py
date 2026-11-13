"""Errors that distinguish API failure from an unfinished task."""

from typing import Any


class ScrapelessReaderError(RuntimeError):
    """The API response could not be accepted as a completed data result."""

    service: str | None = None
    task_id: str | None = None


class ScrapelessTaskError(ScrapelessReaderError):
    """A status request failed after a task ID was obtained; do not resubmit blindly."""

    def __init__(self, service: str, task_id: str) -> None:
        self.service = service
        self.task_id = task_id
        super().__init__(
            f"Status request failed for {service}; inspect the existing task {task_id!r}. "
            "No replacement task was submitted."
        )


class ScrapelessCrawlPageError(ScrapelessReaderError):
    """A completed Crawl result contains failed pages after pagination."""

    def __init__(self, pages: list[Any], failed_indices: list[int]) -> None:
        self.pages = pages
        self.failed_indices = failed_indices
        super().__init__("A completed Crawl page contains an error.")


class ScrapelessTaskTimeout(TimeoutError):
    """Polling stopped; the remote task may continue and can be resumed by ID."""

    def __init__(self, service: str, task_id: str) -> None:
        self.service = service
        self.task_id = task_id
        super().__init__(
            f"Polling deadline exceeded for {service}; resume with task_id={task_id!r}. "
            "The remote task was not cancelled."
        )
