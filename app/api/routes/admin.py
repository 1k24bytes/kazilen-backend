from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import ADMIN_COOKIE_NAME, get_current_admin
from app.core.config import settings
from app.db.database import get_db
from app.db.models import Booking, User
from app.schemas.admin import (
    AdminCreateWorkerRequest,
    AdminLoginRequest,
    AdminProfile,
    AdminStats,
    AdminWorkerResponse,
)
from app.services.admin_service import AdminService

router = APIRouter()


def _set_admin_cookie(response: Response, token: str) -> None:
    """Stores the admin JWT in an HttpOnly cookie (Bearer header also works)."""
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.post("/login", response_model=dict)
def admin_login(request: AdminLoginRequest, response: Response):
    """Authenticate with env-configured admin email + password (bcrypt + JWT)."""
    result = AdminService.login(request)
    _set_admin_cookie(response, result["access_token"])
    return result


@router.post("/logout")
def admin_logout(response: Response):
    """Clears the admin auth cookie."""
    response.delete_cookie(key=ADMIN_COOKIE_NAME, path="/")
    return {"message": "Logged out successfully"}


@router.get("/me", response_model=AdminProfile)
def admin_me(admin: dict = Depends(get_current_admin)):
    """Returns the authenticated admin profile."""
    return AdminProfile(email=admin["email"], role="admin")


@router.get("/stats", response_model=AdminStats)
def admin_stats(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Platform-wide aggregate counts for the admin dashboard."""
    return AdminService.get_stats(db)


@router.post("/workers", response_model=AdminWorkerResponse, status_code=201)
def admin_create_worker(
    request: AdminCreateWorkerRequest,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Creates a worker account directly (admin bypasses phone OTP)."""
    worker = AdminService.create_worker(
        db,
        phone_number=request.phone_number,
        full_name=request.full_name,
        dob=request.dob,
        gender=request.gender,
        is_online=request.is_online if request.is_online is not None else True,
    )
    return AdminWorkerResponse(
        id=worker.id,
        phone_number=worker.phone_number,
        full_name=worker.full_name,
        role=worker.role,
        dob=worker.dob,
        gender=worker.gender,
        referral_code=worker.referral_code,
        is_online=worker.is_online,
    )


@router.get("/users")
def admin_list_users(
    role: str | None = Query(default=None, description="Filter by role: customer | worker"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Paginated user list for the admin panel (newest first)."""
    query = db.query(User)
    if role in ("customer", "worker"):
        query = query.filter(User.role == role)
    total = query.count()
    users = query.order_by(User.id.desc()).offset(offset).limit(limit).all()
    return {
        "status": "success",
        "total": total,
        "users": [
            {
                "id": u.id,
                "phone_number": u.phone_number,
                "full_name": u.full_name,
                "role": u.role,
                "is_online": u.is_online,
                "referral_code": u.referral_code,
                "referral_points": u.referral_points,
                "created_at": str(u.created_at) if u.created_at else None,
            }
            for u in users
        ],
    }


@router.get("/bookings")
def admin_list_bookings(
    booking_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Paginated booking list for the admin panel (newest first)."""
    query = db.query(Booking)
    if booking_status:
        query = query.filter(Booking.status == booking_status)
    total = query.count()
    bookings = query.order_by(Booking.id.desc()).offset(offset).limit(limit).all()
    return {
        "status": "success",
        "total": total,
        "bookings": [
            {
                "id": b.id,
                "customer_id": b.customer_id,
                "worker_id": b.worker_id,
                "service_id": b.service_id,
                "date": b.date,
                "time_slot": b.time_slot,
                "status": b.status,
                "amount": b.amount,
                "created_at": str(b.created_at) if b.created_at else None,
            }
            for b in bookings
        ],
    }
