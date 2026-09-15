"""
Booking business logic layer.

OTP flow:
 1. Customer books → status=pending (no OTP yet)
 2. Worker accepts → status=accepted
 3. Worker at site: generates start_otp → shown on worker screen → worker verifies it → status=in_progress
 4. Job done: Worker generates end_otp → shown on worker screen → worker verifies it → status=completed
"""

import json
import random
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import Booking, User


def _generate_otp() -> str:
    """Returns a random 6-digit OTP string."""
    return str(random.randint(100000, 999999))


def _get_db_session() -> Session:
    return next(get_db())


def _get_worker_availability(worker: User) -> dict:
    """Extracts parsed worker availability config or default empty struct."""
    if not worker or not worker.availability:
        return {"days_off": [], "dead_slots": []}
    try:
        data = json.loads(worker.availability)
        if isinstance(data, dict):
            return {
                "days_off": [str(d).strip() for d in data.get("days_off", []) if isinstance(d, (str, int))],
                "dead_slots": data.get("dead_slots", []) or []
            }
    except Exception:
        pass
    return {"days_off": [], "dead_slots": []}


def _is_day_off(date_str: str, days_off: list) -> tuple[bool, str]:
    """Checks if a given 'YYYY-MM-DD' date string matches any configured day off."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = dt.strftime("%A")  # e.g. "Sunday"
        days_off_lower = [str(d).strip().lower() for d in days_off]
        return day_name.lower() in days_off_lower, day_name
    except Exception:
        return False, ""


def _parse_dead_slot_hours(dead_slots: list) -> list[int]:
    """Expands dead slot ranges like '13:00'-'15:00' into [13, 14]."""
    hours = set()
    for slot in dead_slots:
        if isinstance(slot, dict):
            start_str = slot.get("start_time", "")
            end_str = slot.get("end_time", "")
            try:
                start_h = int(start_str.split(":")[0])
                end_h = int(end_str.split(":")[0])
                for h in range(start_h, end_h):
                    hours.add(h)
            except Exception:
                pass
    return sorted(hours)


# ---------------------------------------------------------------------------
# Customer: create booking
# ---------------------------------------------------------------------------

def create_booking(booking_data, customer_id: int) -> Booking:
    db = _get_db_session()
    new_slot = booking_data.time_slot

    # Validate worker availability
    worker = db.query(User).filter(User.id == booking_data.worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Service partner not found.")

    if worker.is_online == 0:
        raise HTTPException(
            status_code=400,
            detail="This specialist is currently offline and unavailable for booking.",
        )

    from app.services.membership_service import MembershipService
    MembershipService.check_booking_allowed(worker.id, db)

    avail = _get_worker_availability(worker)
    days_off = avail.get("days_off", [])
    dead_slots = avail.get("dead_slots", [])

    is_off, day_name = _is_day_off(booking_data.date, days_off)
    if is_off:
        raise HTTPException(
            status_code=409,
            detail=f"This service partner is not available on {day_name}s (Scheduled Day Off).",
        )

    # Fetch all active bookings for this worker on this date
    existing = (
        db.query(Booking)
        .filter(
            Booking.worker_id == booking_data.worker_id,
            Booking.date == booking_data.date,
            Booking.status.in_(["pending", "accepted", "in_progress"]),
        )
        .all()
    )

    is_asap = "ASAP" in new_slot.upper() or "INSTANT" in new_slot.upper()
    if is_asap:
        for b in existing:
            if b.customer_id == customer_id and ("ASAP" in b.time_slot.upper() or "INSTANT" in b.time_slot.upper()):
                raise HTTPException(
                    status_code=409,
                    detail="You already have an active Instant ASAP booking with this worker.",
                )
    else:
        # Check against worker's configured dead hours
        req_hours = set(_parse_slot_hours(new_slot))
        dead_hours = set(_parse_dead_slot_hours(dead_slots))
        if req_hours & dead_hours:
            raise HTTPException(
                status_code=409,
                detail="This service partner is unavailable during that time (Scheduled Break / Dead Hours).",
            )

        for b in existing:
            if _slots_overlap(new_slot, b.time_slot):
                # Distinguish: is it the same customer re-booking?
                if b.customer_id == customer_id:
                    raise HTTPException(
                        status_code=409,
                        detail="You already have an active booking for this worker at this time.",
                    )
                raise HTTPException(
                    status_code=409,
                    detail="This worker is already booked during that time. Please choose a different slot.",
                )

    booking = Booking(
        customer_id=customer_id,
        worker_id=booking_data.worker_id,
        service_id=booking_data.service_id,
        date=booking_data.date,
        time_slot=booking_data.time_slot,
        address=booking_data.address,
        amount=booking_data.amount,
        status="pending",
    )
    db.add(booking)
    db.commit()
    db.refresh(booking)
    print(f"[BOOKING] New booking #{booking.id} created for customer {customer_id}, worker {booking_data.worker_id}")
    booking.worker_name = worker.full_name or "Verified Partner"
    customer = db.query(User).filter(User.id == customer_id).first()
    booking.customer_name = customer.full_name if customer else "Customer"
    return booking



# ---------------------------------------------------------------------------
# Public: get booked slots for a worker on a date
# ---------------------------------------------------------------------------

def _parse_slot_hours(time_slot: str) -> list[int]:
    """Parse 'HH:MM-HH:MM' → list of occupied start-hours (ints).
    E.g. '09:00-11:00' → [9, 10]."""
    try:
        start_str, end_str = time_slot.split("-", 1)
        start_h = int(start_str.split(":")[0])
        end_h = int(end_str.split(":")[0])
        return list(range(start_h, end_h))
    except Exception:
        return []


def _slots_overlap(a: str, b: str) -> bool:
    """Return True if time slot strings a and b share at least one hour."""
    return bool(set(_parse_slot_hours(a)) & set(_parse_slot_hours(b)))


# ---------------------------------------------------------------------------
# Public: get booked slots for a worker on a date
# ---------------------------------------------------------------------------

def get_booked_slots(worker_id: int, date: str) -> dict:
    """Returns individual booked hour-start strings for the worker on the date,
    including worker's scheduled dead time zones and masking if the day is a day off.
    E.g. if '09:00-11:00' is booked → returns ['09:00', '10:00']."""
    db = _get_db_session()

    worker = db.query(User).filter(User.id == worker_id).first()
    avail = _get_worker_availability(worker)
    days_off = avail.get("days_off", [])
    dead_slots = avail.get("dead_slots", [])

    is_off, day_name = _is_day_off(date, days_off)
    if is_off:
        # Mask all slots for the full day off (09:00 to 21:00)
        all_hours = [f"{str(h).zfill(2)}:00" for h in range(9, 21)]
        return {
            "booked_hours": all_hours,
            "is_day_off": True,
            "day_off_name": day_name,
            "days_off": days_off,
            "dead_hours": []
        }

    rows = (
        db.query(Booking.time_slot)
        .filter(
            Booking.worker_id == worker_id,
            Booking.date == date,
            Booking.status.in_(["pending", "accepted", "in_progress"]),
        )
        .all()
    )
    occupied = set()
    for (slot,) in rows:
        for h in _parse_slot_hours(slot):
            occupied.add(f"{str(h).zfill(2)}:00")

    # Add worker dead time hours to occupied hours
    dead_hours = []
    for h in _parse_dead_slot_hours(dead_slots):
        hour_str = f"{str(h).zfill(2)}:00"
        occupied.add(hour_str)
        dead_hours.append(hour_str)

    return {
        "booked_hours": sorted(occupied),
        "is_day_off": False,
        "day_off_name": None,
        "days_off": days_off,
        "dead_hours": sorted(dead_hours)
    }



# ---------------------------------------------------------------------------
# Customer: list own bookings
# ---------------------------------------------------------------------------

def get_customer_bookings(customer_id: int) -> dict:
    db = _get_db_session()
    bookings = (
        db.query(Booking)
        .filter(Booking.customer_id == customer_id)
        .order_by(Booking.created_at.desc())
        .all()
    )
    worker_ids = {b.worker_id for b in bookings}
    worker_map = {}
    if worker_ids:
        workers = db.query(User).filter(User.id.in_(worker_ids)).all()
        worker_map = {w.id: (w.full_name or "Verified Partner") for w in workers}

    for b in bookings:
        b.worker_name = worker_map.get(b.worker_id, "Verified Partner")

    return {"status": "success", "bookings": bookings}


# ---------------------------------------------------------------------------
# Worker: list incoming bookings
# ---------------------------------------------------------------------------

def get_worker_bookings(worker_id: int) -> dict:
    """Returns pending, accepted and in_progress bookings for the worker."""
    db = _get_db_session()
    bookings = (
        db.query(Booking)
        .filter(
            Booking.worker_id == worker_id,
            Booking.status.in_(["pending", "accepted", "in_progress"])
        )
        .order_by(Booking.created_at.desc())
        .all()
    )
    cust_ids = {b.customer_id for b in bookings}
    cust_map = {}
    if cust_ids:
        customers = db.query(User).filter(User.id.in_(cust_ids)).all()
        cust_map = {c.id: (c.full_name or "Customer") for c in customers}

    worker = db.query(User).filter(User.id == worker_id).first()
    w_name = worker.full_name if worker else "Verified Partner"

    for b in bookings:
        b.customer_name = cust_map.get(b.customer_id, "Customer")
        b.worker_name = w_name

    return {"status": "success", "bookings": bookings}


# ---------------------------------------------------------------------------
# Shared: get single booking detail
# ---------------------------------------------------------------------------

def get_booking_detail(booking_id: int, user_id: int) -> Booking:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.customer_id != user_id and booking.worker_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    worker = db.query(User).filter(User.id == booking.worker_id).first()
    customer = db.query(User).filter(User.id == booking.customer_id).first()
    booking.worker_name = worker.full_name if worker else "Verified Partner"
    booking.customer_name = customer.full_name if customer else "Customer"
    return booking


# ---------------------------------------------------------------------------
# Worker: accept booking
# ---------------------------------------------------------------------------

def accept_booking(booking_id: int, worker_id: int) -> dict:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="This booking is not assigned to you")
    if booking.status != "pending":
        raise HTTPException(status_code=400, detail=f"Booking is already {booking.status}")
    from app.services.membership_service import MembershipService
    quota = MembershipService.worker_quota(worker_id, db)
    if quota["subscribed"]:
        limit = quota["plan_limit"]
        used = quota["plan_used"]
    else:
        limit = quota["free_limit"]
        used = quota["total_used"]
    if limit is not None and used >= limit:
        if quota["subscribed"]:
            detail = (
                f"Booking limit reached ({used}/{limit} on {quota['plan_name']}). "
                "Renew the subscription to accept more jobs."
            )
        else:
            detail = (
                f"Free bookings used ({used}/{quota['free_limit']}). "
                "Subscribe to a membership plan to accept more jobs."
            )
        raise HTTPException(status_code=402, detail=detail)
    booking.status = "accepted"
    db.commit()
    print(f"[BOOKING] #{booking_id} accepted by worker {worker_id}")
    return {"status": "accepted", "booking_id": booking_id, "message": "Booking accepted"}


# ---------------------------------------------------------------------------
# Worker: generate start OTP (to begin job at customer site)
# ---------------------------------------------------------------------------

def generate_start_otp(booking_id: int, worker_id: int) -> dict:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if booking.status != "accepted":
        raise HTTPException(status_code=400, detail=f"Cannot generate OTP. Booking status is '{booking.status}'. Must be 'accepted'.")
    otp = _generate_otp()
    booking.start_otp = otp
    db.commit()
    print(f"[BOOKING] Start OTP for #{booking_id}: {otp}")
    return {"status": "success", "otp": otp, "booking_id": booking_id}


# ---------------------------------------------------------------------------
# Worker: verify start OTP → transitions to in_progress
# ---------------------------------------------------------------------------

def verify_start_otp(booking_id: int, worker_id: int, otp: str) -> dict:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not booking.start_otp:
        raise HTTPException(status_code=400, detail="Start OTP has not been generated yet")
    if booking.start_otp != otp and otp != "123456":  # dev master bypass
        raise HTTPException(status_code=400, detail="Incorrect OTP. Please try again.")
    booking.status = "in_progress"
    db.commit()
    print(f"[BOOKING] #{booking_id} start OTP verified — job in progress")
    return {"status": "in_progress", "booking_id": booking_id, "message": "Job started successfully"}


# ---------------------------------------------------------------------------
# Worker: generate end OTP (to close job)
# ---------------------------------------------------------------------------

def generate_end_otp(booking_id: int, worker_id: int) -> dict:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if booking.status != "in_progress":
        raise HTTPException(status_code=400, detail=f"Cannot generate end OTP. Booking status is '{booking.status}'. Must be 'in_progress'.")
    otp = _generate_otp()
    booking.end_otp = otp
    db.commit()
    print(f"[BOOKING] End OTP for #{booking_id}: {otp}")
    return {"status": "success", "otp": otp, "booking_id": booking_id}


# ---------------------------------------------------------------------------
# Worker: verify end OTP → transitions to completed
# ---------------------------------------------------------------------------

def verify_end_otp(booking_id: int, worker_id: int, otp: str) -> dict:
    db = _get_db_session()
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not booking.end_otp:
        raise HTTPException(status_code=400, detail="End OTP has not been generated yet")
    if booking.end_otp != otp and otp != "123456":  # dev master bypass
        raise HTTPException(status_code=400, detail="Incorrect OTP. Please try again.")
    booking.status = "completed"
    db.commit()
    print(f"[BOOKING] #{booking_id} end OTP verified — job completed")
    return {"status": "completed", "booking_id": booking_id, "message": "Job completed successfully"}
