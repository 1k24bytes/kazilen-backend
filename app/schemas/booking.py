from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List, Union


class BookingCreate(BaseModel):
    worker_id: int
    service_id: str
    date: str           # YYYY-MM-DD
    time_slot: str      # "HH:MM" (start of 1-hour slot)
    address: str
    amount: Optional[str] = None   # price string e.g. "299"
    discount_code: Optional[str] = None  # promo code for all/specific customers


class BookingResponse(BaseModel):
    id: int
    customer_id: int
    worker_id: int
    worker_name: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    worker_phone: Optional[str] = None
    service_id: str
    date: str
    time_slot: str
    status: str
    address: Optional[str] = None
    amount: Optional[str] = None
    discount_code: Optional[str] = None
    discount_amount: Optional[str] = None
    start_otp: Optional[str] = None
    end_otp: Optional[str] = None
    created_at: Optional[Union[datetime, str]] = None

    class Config:
        from_attributes = True


class BookingDetailResponse(BaseModel):
    """Full booking detail — returned for single booking GET."""
    id: int
    customer_id: int
    worker_id: int
    worker_name: Optional[str] = None
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    worker_phone: Optional[str] = None
    service_id: str
    date: str
    time_slot: str
    status: str
    address: Optional[str] = None
    amount: Optional[str] = None
    discount_code: Optional[str] = None
    discount_amount: Optional[str] = None
    start_otp: Optional[str] = None   # only exposed to relevant party
    end_otp: Optional[str] = None     # only exposed to relevant party
    created_at: Optional[Union[datetime, str]] = None

    class Config:
        from_attributes = True



class BookingListResponse(BaseModel):
    status: str
    bookings: List[BookingResponse]


class OTPRequest(BaseModel):
    otp: str


class OTPGenerateResponse(BaseModel):
    status: str
    otp: str
    booking_id: int


class BookingActionResponse(BaseModel):
    status: str
    booking_id: int
    message: Optional[str] = None
