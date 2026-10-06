"""One page of a list, for the list endpoints."""

from typing import Any, TypeVar

PAGE_SIZE = 100

T = TypeVar("T")


def paginate(items: list[T], page: int, page_size: int) -> tuple[list[T], dict[str, Any]]:
    """The items on ``page`` and the paging fields of the response.

    A page past the end becomes the last page, so a list that shrank while it
    was open still shows something.
    """
    total = len(items)
    page = min(max(page, 1), max(1, -(-total // page_size)))
    start = (page - 1) * page_size
    return items[start : start + page_size], {
        "total": total,
        "page": page,
        "page_size": page_size,
    }
