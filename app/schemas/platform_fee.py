from typing import Optional

from pydantic import BaseModel, Field


class PlatformFeeSlabCreate(BaseModel):
    min_amount: int = Field(..., ge=0)
    max_amount: Optional[int] = Field(default=None, ge=0)
    fee_rs: int = Field(..., ge=0)
    is_active: bool = True
    sort_order: int = 0


class PlatformFeeSlabUpdate(BaseModel):
    min_amount: Optional[int] = Field(default=None, ge=0)
    max_amount: Optional[int] = Field(default=None, ge=0)
    fee_rs: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


class PlatformFeeSlabResponse(BaseModel):
    id: int
    min_amount: int
    max_amount: Optional[int] = None
    fee_rs: int
    is_active: bool
    sort_order: int = 0

    class Config:
        from_attributes = True


class BookingPaymentVerifyRequest(BaseModel):
    razorpay_order_id: str = Field(..., min_length=1)
    razorpay_payment_id: str = Field(..., min_length=1)
    razorpay_signature: str = Field(..., min_length=1)
