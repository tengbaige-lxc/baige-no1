from typing import Generic, TypeVar, Optional, List
from pydantic import BaseModel

T = TypeVar("T")


class ResponseModel(BaseModel, Generic[T]):
    code: int = 200
    message: str = "success"
    data: Optional[T] = None


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = 10


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
