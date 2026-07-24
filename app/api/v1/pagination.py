"""Shared pagination for API list endpoints.

Backward compatible by design: without limit/offset the endpoints return
the complete bare array they always did; with them, the same array is
sliced and the pre-pagination total travels in the X-Total-Count header.
Slicing happens here, over the domain's results — dataset sizes are
single-operator scale (plan NFRs), and this keeps pagination out of the
domain until volume ever justifies pushing it into SQL.
"""

from typing import Annotated

from fastapi import Depends, Query, Response

TOTAL_COUNT_HEADER = "X-Total-Count"

# OpenAPI documentation for paginated 200 responses.
PAGINATED_200 = {
    200: {
        "headers": {
            TOTAL_COUNT_HEADER: {
                "description": "Total number of items before limit/offset were applied.",
                "schema": {"type": "integer"},
            }
        }
    }
}


class PageParams:
    def __init__(
        self,
        limit: Annotated[
            int | None,
            Query(ge=1, le=500, description="Maximum items to return; omit for all."),
        ] = None,
        offset: Annotated[int, Query(ge=0, description="Items to skip from the start.")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


PageDep = Annotated[PageParams, Depends()]


def paginate[T](items: list[T], page: PageParams, response: Response) -> list[T]:
    response.headers[TOTAL_COUNT_HEADER] = str(len(items))
    end = None if page.limit is None else page.offset + page.limit
    return items[page.offset : end]
