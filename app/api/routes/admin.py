from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.api.deps import ADMIN_COOKIE_NAME, get_current_admin
from app.core.config import settings
from app.db.database import get_db
from app.db.models import Booking, BookingReview, MembershipSubscription, PlatformFeedback, ReferralClaim, User
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
        verification_status=worker.verification_status or "approved",
        profile_photo=worker.profile_photo,
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
                "verification_status": u.verification_status or "approved",
                "profile_photo": u.profile_photo,
                "referral_code": u.referral_code,
                "referral_points": u.referral_points,
                "created_at": str(u.created_at) if u.created_at else None,
            }
            for u in users
        ],
    }


@router.get("/users/{user_id}/quota")
def admin_user_quota(
    user_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Live quota for a worker (free tier + active plan usage)."""
    from fastapi import HTTPException
    from app.services.membership_service import MembershipService
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "worker":
        raise HTTPException(status_code=400, detail="Quota applies to workers only")
    return {"status": "success", "quota": MembershipService.worker_quota(user_id, db)}


@router.get("/users/{user_id}")
def admin_get_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Single user detail with bookings, reviews and referral stats."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    bookings_count = db.query(Booking).filter(
        (Booking.customer_id == user_id) | (Booking.worker_id == user_id)
    ).count()
    reviews_received = db.query(BookingReview).filter(BookingReview.reviewee_id == user_id).count()
    referrals_made = db.query(ReferralClaim).filter(ReferralClaim.referrer_id == user_id).count()
    return {
        "status": "success",
        "user": {
            "id": user.id,
            "phone_number": user.phone_number,
            "full_name": user.full_name,
            "role": user.role,
            "dob": user.dob,
            "gender": user.gender,
            "is_online": user.is_online,
            "verification_status": user.verification_status or "approved",
            "profile_photo": user.profile_photo,
            "referral_code": user.referral_code,
            "referral_points": user.referral_points,
            "created_at": str(user.created_at) if user.created_at else None,
        },
        "stats": {
            "bookings_count": bookings_count,
            "reviews_received": reviews_received,
            "referrals_made": referrals_made,
        },
    }


@router.put("/users/{user_id}")
def admin_update_user(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update name, online status, or role for a user."""
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "full_name" in payload and payload["full_name"] is not None:
        user.full_name = str(payload["full_name"]).strip() or user.full_name
    if "is_online" in payload and payload["is_online"] is not None:
        user.is_online = 1 if payload["is_online"] else 0
    if "role" in payload and payload["role"] in ("customer", "worker"):
        user.role = payload["role"]
    if "verification_status" in payload and payload["verification_status"] in ("pending", "approved", "rejected"):
        user.verification_status = payload["verification_status"]
    db.commit()
    db.refresh(user)
    return {
        "status": "success",
        "user": {
            "id": user.id,
            "phone_number": user.phone_number,
            "full_name": user.full_name,
            "role": user.role,
            "is_online": user.is_online,
            "verification_status": user.verification_status or "approved",
            "profile_photo": user.profile_photo,
            "referral_code": user.referral_code,
            "referral_points": user.referral_points,
        },
    }


@router.put("/users/{user_id}/verification")
def admin_verify_user(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Approves or rejects a worker (`action`: approve | reject). Approved workers go live in the marketplace."""
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    action = (payload.get("action") or "").strip().lower()
    if action == "approve":
        user.verification_status = "approved"
    elif action == "reject":
        user.verification_status = "rejected"
    else:
        raise HTTPException(status_code=400, detail="action must be approve or reject")
    db.commit()
    db.refresh(user)
    return {
        "status": "success",
        "user": {
            "id": user.id,
            "full_name": user.full_name,
            "role": user.role,
            "verification_status": user.verification_status,
            "profile_photo": user.profile_photo,
        },
    }


@router.delete("/users/{user_id}")
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a user account (blocked when bookings exist)."""
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    bookings_count = db.query(Booking).filter(
        (Booking.customer_id == user_id) | (Booking.worker_id == user_id)
    ).count()
    if bookings_count:
        raise HTTPException(
            status_code=409,
            detail=f"User has {bookings_count} bookings and cannot be deleted. Set offline instead.",
        )
    db.delete(user)
    db.commit()
    return {"status": "success", "message": "User deleted"}


@router.get("/reviews")
def admin_list_reviews(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Paginated participant reviews (newest first) with names."""
    query = (
        db.query(BookingReview, Booking)
        .join(Booking, Booking.id == BookingReview.booking_id)
        .order_by(BookingReview.id.desc())
    )
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    reviewer_ids = {r.reviewer_id for r, _ in rows} | {r.reviewee_id for r, _ in rows}
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(reviewer_ids)).all()} if reviewer_ids else {}
    return {
        "status": "success",
        "total": total,
        "reviews": [
            {
                "id": r.id,
                "booking_id": r.booking_id,
                "service_id": b.service_id,
                "reviewer_id": r.reviewer_id,
                "reviewer_name": names.get(r.reviewer_id),
                "reviewee_id": r.reviewee_id,
                "reviewee_name": names.get(r.reviewee_id),
                "rating": r.rating,
                "description": r.description,
                "created_at": str(r.created_at) if r.created_at else None,
            }
            for r, b in rows
        ],
    }


@router.delete("/reviews/{review_id}")
def admin_delete_review(
    review_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Remove an abusive or invalid participant review."""
    from fastapi import HTTPException
    review = db.query(BookingReview).filter(BookingReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    db.delete(review)
    db.commit()
    return {"status": "success", "message": "Review deleted"}


@router.get("/feedback")
def admin_list_feedback(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Paginated platform feedback (newest first) with user names."""
    query = db.query(PlatformFeedback).order_by(PlatformFeedback.id.desc())
    total = query.count()
    rows = query.offset(offset).limit(limit).all()
    user_ids = {f.user_id for f in rows}
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return {
        "status": "success",
        "total": total,
        "feedback": [
            {
                "id": f.id,
                "booking_id": f.booking_id,
                "user_id": f.user_id,
                "user_name": names.get(f.user_id),
                "rating": f.rating,
                "description": f.description,
                "created_at": str(f.created_at) if f.created_at else None,
            }
            for f in rows
        ],
    }


@router.delete("/feedback/{feedback_id}")
def admin_delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Remove a platform feedback record."""
    from fastapi import HTTPException
    feedback = db.query(PlatformFeedback).filter(PlatformFeedback.id == feedback_id).first()
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found")
    db.delete(feedback)
    db.commit()
    return {"status": "success", "message": "Feedback deleted"}


@router.get("/referrals")
def admin_list_referrals(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Referral leaderboard plus recent claims."""
    leaders = (
        db.query(User)
        .filter(User.referral_points > 0)
        .order_by(User.referral_points.desc(), User.id.desc())
        .limit(50)
        .all()
    )
    claims_query = db.query(ReferralClaim).order_by(ReferralClaim.id.desc())
    total = claims_query.count()
    claims = claims_query.offset(offset).limit(limit).all()
    user_ids = {c.referrer_id for c in claims} | {c.referred_user_id for c in claims}
    names = {u.id: u.full_name for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return {
        "status": "success",
        "total": total,
        "leaders": [
            {
                "id": u.id,
                "full_name": u.full_name,
                "phone_number": u.phone_number,
                "role": u.role,
                "referral_code": u.referral_code,
                "referral_points": u.referral_points,
            }
            for u in leaders
        ],
        "claims": [
            {
                "id": c.id,
                "referrer_id": c.referrer_id,
                "referrer_name": names.get(c.referrer_id),
                "referred_user_id": c.referred_user_id,
                "referred_name": names.get(c.referred_user_id),
                "referral_code": c.referral_code,
                "created_at": str(c.created_at) if c.created_at else None,
            }
            for c in claims
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
