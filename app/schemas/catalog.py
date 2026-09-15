from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CategoryBase(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    image: Optional[str] = None
    is_active: Optional[bool] = True
    sort_order: Optional[int] = 0


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class CategoryResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    image: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0
    sub_categories_count: int = 0


class SubCategoryBase(BaseModel):
    id: str = Field(..., min_length=1, max_length=100)
    category_id: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    image: Optional[str] = None
    tag: Optional[str] = None
    price_type: str = Field(default="fixed", pattern="^(fixed|hourly)$")
    fixed_price: Optional[int] = None
    price_per_hour: Optional[int] = None
    is_active: Optional[bool] = True
    sort_order: Optional[int] = 0


class SubCategoryCreate(SubCategoryBase):
    pass


class SubCategoryUpdate(BaseModel):
    category_id: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    tag: Optional[str] = None
    price_type: Optional[str] = Field(default=None, pattern="^(fixed|hourly)$")
    fixed_price: Optional[int] = None
    price_per_hour: Optional[int] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class SubCategoryResponse(BaseModel):
    id: str
    category_id: str
    label: str
    description: Optional[str] = None
    image: Optional[str] = None
    tag: Optional[str] = None
    price_type: str = "fixed"
    fixed_price: Optional[int] = None
    price_per_hour: Optional[int] = None
    is_active: bool = True
    sort_order: int = 0


class CatalogResponse(BaseModel):
    categories: List[CategoryResponse]
    sub_categories: List[SubCategoryResponse]


class CatalogSyncResponse(BaseModel):
    status: str
    categories_upserted: int
    sub_categories_upserted: int
    synced_at: Optional[datetime] = None
