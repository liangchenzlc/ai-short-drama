from pydantic import BaseModel


class PageResponse[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int
