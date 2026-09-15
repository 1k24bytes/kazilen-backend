from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class MembershipPlanBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    price: int = Field(default=0, ge=0)
    duration_days: int = Field(default=30, ge=1)
    bookings_included: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_active: Optional[bool] = True


class MembershipPlanCreate(MembershipPlanBase):
    pass


class MembershipPlanUpdate(BaseModel):
    name: Optional[str] = None
    price: Optional[int] = Field(default=None, ge=0)
    duration_days: Optional[int] = Field(default=None, ge=1)
    bookings_included: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = None
    features: Optional[List[str]] = None
    is_active: Optional[bool] = None


class MembershipPlanResponse(BaseModel):
    id: int
    name: str
    price: int
    duration_days: int
    bookings_included: Optional[int] = None
    description: Optional[str] = None
    features: List[str] = []
    is_active: bool = True
    subscribers_count: int = 0


class MembershipSubscriptionCreate(BaseModel):
    worker_id: int
    plan_id: int
    status: str = Field(default="active", pattern="^(active|expired|cancelled)$")


class MembershipSubscriptionResponse(BaseModel):
    id: int
    worker_id: int
    worker_name: Optional[str] = None
    plan_id: int
    plan_name: Optional[str] = None
    status: str
    starts_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
