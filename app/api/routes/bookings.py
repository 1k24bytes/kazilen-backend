"""
Booking API routes.

All paths here are relative to the prefix /api/bookings mounted in main.py.

Endpoints:
  POST  /book                         — Customer creates a booking
  GET   /my                           — Customer lists own bookings
  GET   /worker/pending               — Worker lists pending/active bookings
  GET   /{id}                         — Shared: get single booking detail
  POST  /{id}/accept                  — Worker accepts a pending booking
  POST  /{id}/generate-start-otp      — Worker generates start OTP at site
  POST  /{id}/verify-start-otp        — Worker verifies start OTP → in_progress
  POST  /{id}/generate-end-otp        — Worker generates end OTP to close job
  POST  /{id}/verify-end-otp          — Worker verifies end OTP → completed
"""


from app.api.deps import get_current_user
from app.schemas.booking import (
    BookingCreate,
    BookingResponse,
    BookingDetailResponse,
    BookingListResponse,
    OTPRequest,
    OTPGenerateResponse,
    BookingActionResponse,
)
from fastapi import APIRouter, Depends, Query
from app.services.booking_service import (
    create_booking,
    get_customer_bookings,
    get_worker_bookings,
    get_booking_detail,
    get_booked_slots,
    accept_booking,
    generate_start_otp,
    verify_start_otp,
    generate_end_otp,
    verify_end_otp,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Public: booked slots for a worker on a given date (no auth needed)
# ---------------------------------------------------------------------------

@router.get("/worker-slots")
def get_worker_slots_endpoint(
    worker_id: int = Query(...),
    date: str = Query(...),
):
    """Returns list of already-booked time_slot strings for a worker on a date.
    Used by customer UI to grey out unavailable slots."""
    return get_booked_slots(worker_id, date)


# ---------------------------------------------------------------------------
# Customer endpoints
# ---------------------------------------------------------------------------

@router.get("/validate-discount")
def validate_discount_endpoint(
    code: str = Query(...),
    amount: int = Query(default=0),
    current_user=Depends(get_current_user),
):
    """Validates a discount code for the logged-in customer without creating a booking."""
    from app.db.database import get_db
    from app.services.discount_service import DiscountService
    db = next(get_db())
    return {"status": "success", **DiscountService.validate(code, current_user.id, amount, db)}


@router.post("/book", response_model=BookingResponse)
def create_booking_endpoint(
    booking: BookingCreate,
    current_user=Depends(get_current_user),
):
    """Customer creates a booking. Returns created booking (no OTP at this stage)."""
    return create_booking(booking, current_user.id)


@router.get("/my", response_model=BookingListResponse)
def get_my_bookings_endpoint(current_user=Depends(get_current_user)):
    """Customer retrieves all their own bookings (all statuses)."""
    return get_customer_bookings(current_user.id)


# ---------------------------------------------------------------------------
# Worker endpoints
# ---------------------------------------------------------------------------

@router.get("/worker/pending", response_model=BookingListResponse)
def get_worker_bookings_endpoint(current_user=Depends(get_current_user)):
    """Worker lists their pending, accepted, and in_progress bookings."""
    return get_worker_bookings(current_user.id)


# ---------------------------------------------------------------------------
# Shared: single booking detail
# ---------------------------------------------------------------------------

@router.get("/{booking_id}", response_model=BookingDetailResponse)
def get_booking_detail_endpoint(
    booking_id: int,
    current_user=Depends(get_current_user),
):
    """Returns full detail of a booking. Accessible by the customer or the assigned worker."""
    return get_booking_detail(booking_id, current_user.id)


# ---------------------------------------------------------------------------
# Worker: booking lifecycle
# ---------------------------------------------------------------------------

@router.post("/{booking_id}/accept", response_model=BookingActionResponse)
def accept_booking_endpoint(
    booking_id: int,
    current_user=Depends(get_current_user),
):
    """Worker accepts a pending booking → status becomes 'accepted'."""
    return accept_booking(booking_id, current_user.id)


@router.post("/{booking_id}/generate-start-otp", response_model=OTPGenerateResponse)
def generate_start_otp_endpoint(
    booking_id: int,
    current_user=Depends(get_current_user),
):
    """Worker generates the start OTP at the customer's site. OTP is shown on worker screen."""
    return generate_start_otp(booking_id, current_user.id)


@router.post("/{booking_id}/verify-start-otp", response_model=BookingActionResponse)
def verify_start_otp_endpoint(
    booking_id: int,
    body: OTPRequest,
    current_user=Depends(get_current_user),
):
    """Worker enters the OTP to confirm job start → status becomes 'in_progress'."""
    return verify_start_otp(booking_id, current_user.id, body.otp)


@router.post("/{booking_id}/generate-end-otp", response_model=OTPGenerateResponse)
def generate_end_otp_endpoint(
    booking_id: int,
    current_user=Depends(get_current_user),
):
    """Worker generates end OTP after completing the job."""
    return generate_end_otp(booking_id, current_user.id)


@router.post("/{booking_id}/verify-end-otp", response_model=BookingActionResponse)
def verify_end_otp_endpoint(
    booking_id: int,
    body: OTPRequest,
    current_user=Depends(get_current_user),
):
    """Worker enters end OTP to close the job → status becomes 'completed'."""
    return verify_end_otp(booking_id, current_user.id, body.otp)
