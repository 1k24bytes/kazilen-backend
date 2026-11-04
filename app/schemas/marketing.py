from typing import Optional

from pydantic import BaseModel, Field


class OfferCreate(BaseModel):
    title: str = Field(..., min_length=1)
    subtitle: Optional[str] = None
    image: Optional[str] = None
    link_type: str = "none"
    link_target: Optional[str] = None
    discount_code: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


class OfferUpdate(BaseModel):
    title: Optional[str] = None
    subtitle: Optional[str] = None
    image: Optional[str] = None
    link_type: Optional[str] = None
    link_target: Optional[str] = None
    discount_code: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class PopupCreate(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    image: Optional[str] = None
    cta_text: Optional[str] = None
    cta_link: Optional[str] = None
    discount_code: Optional[str] = None
    delay_seconds: int = Field(default=5, ge=1, le=60)
    is_active: bool = False


class PopupUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None
    cta_text: Optional[str] = None
    cta_link: Optional[str] = None
    discount_code: Optional[str] = None
    delay_seconds: Optional[int] = Field(default=None, ge=1, le=60)
    is_active: Optional[bool] = None


class BookingConfigUpdate(BaseModel):
    auto_confirm: bool


class SupportTicketCreate(BaseModel):
    category: str = Field(default="query")
    subject: Optional[str] = None
    description: str = Field(..., min_length=6)
    booking_id: Optional[int] = None


class SupportTicketReply(BaseModel):
    status: Optional[str] = None
    admin_reply: Optional[str] = None
