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
                "is_blocked": bool(getattr(u, "is_blocked", 0) or 0),
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


@router.get("/workers/{user_id}/services")
def admin_get_worker_services(
    user_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """List a worker's offered services with prices and lock flags."""
    import json
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "worker":
        raise HTTPException(status_code=400, detail="Services apply to workers only")
    services = []
    if user.offered_services:
        try:
            parsed = json.loads(user.offered_services)
            services = parsed if isinstance(parsed, list) else [parsed]
        except Exception:
            services = str(user.offered_services).split(",")
    return {
        "status": "success",
        "worker_id": user.id,
        "worker_name": user.full_name,
        "services": services,
    }


@router.put("/workers/{user_id}/services")
def admin_update_worker_services(
    user_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Admin sets worker service prices and lock flags. Merges by service id."""
    import json
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role != "worker":
        raise HTTPException(status_code=400, detail="Services apply to workers only")
    incoming = payload.get("services")
    if not isinstance(incoming, list):
        raise HTTPException(status_code=400, detail="services must be a list")
    current = []
    if user.offered_services:
        try:
            parsed = json.loads(user.offered_services)
            current = parsed if isinstance(parsed, list) else []
        except Exception:
            current = []
    by_id = {}
    for item in current:
        if isinstance(item, dict) and item.get("id"):
            by_id[str(item["id"])] = item
        elif isinstance(item, str):
            by_id[str(item)] = {"id": str(item)}
    for item in incoming:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        sid = str(item["id"])
        existing = by_id.get(sid, {"id": sid})
        if "price" in item and item["price"] is not None:
            try:
                existing["price"] = int(item["price"])
            except (TypeError, ValueError):
                pass
        if "price_type" in item and item["price_type"] in ("fixed", "hourly"):
            existing["price_type"] = item["price_type"]
        if "price_locked" in item:
            existing["price_locked"] = bool(item["price_locked"])
        if "fixed_price" in item and item["fixed_price"] is not None:
            try:
                existing["fixed_price"] = int(item["fixed_price"])
            except (TypeError, ValueError):
                pass
        if "price_per_hour" in item and item["price_per_hour"] is not None:
            try:
                existing["price_per_hour"] = int(item["price_per_hour"])
            except (TypeError, ValueError):
                pass
        existing["enabled"] = True
        by_id[sid] = existing
    merged = list(by_id.values())
    user.offered_services = json.dumps(merged)
    db.commit()
    db.refresh(user)
    return {"status": "success", "worker_id": user.id, "services": merged}


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
            "is_blocked": bool(getattr(user, "is_blocked", 0) or 0),
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
    """Update name, online status, block flag, or role for a user."""
    from fastapi import HTTPException
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if "full_name" in payload and payload["full_name"] is not None:
        user.full_name = str(payload["full_name"]).strip() or user.full_name
    if "is_online" in payload and payload["is_online"] is not None:
        user.is_online = 1 if payload["is_online"] else 0
    if "is_blocked" in payload and payload["is_blocked"] is not None:
        user.is_blocked = 1 if payload["is_blocked"] else 0
        if payload["is_blocked"]:
            user.is_online = 0
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
            "is_blocked": bool(getattr(user, "is_blocked", 0) or 0),
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


@router.put("/reviews/{review_id}")
def admin_update_review(
    review_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Edit a participant review's rating and description."""
    from fastapi import HTTPException
    review = db.query(BookingReview).filter(BookingReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if "rating" in payload and payload["rating"] is not None:
        try:
            rating = int(payload["rating"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Rating must be 1-5")
        if rating < 1 or rating > 5:
            raise HTTPException(status_code=400, detail="Rating must be 1-5")
        review.rating = rating
    if "description" in payload and payload["description"] is not None:
        text_val = str(payload["description"]).strip()
        if not text_val:
            raise HTTPException(status_code=400, detail="Description cannot be empty")
        if len(text_val) > 2000:
            raise HTTPException(status_code=400, detail="Description too long (max 2000)")
        review.description = text_val
    db.commit()
    db.refresh(review)
    return {
        "status": "success",
        "review": {
            "id": review.id,
            "booking_id": review.booking_id,
            "rating": review.rating,
            "description": review.description,
        },
    }


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
    # Reward config (both | referrer_only + Rs amounts), auto-seeded on first read.
    from app.db.models import ReferralConfig
    config = db.query(ReferralConfig).filter(ReferralConfig.id == 1).first()
    if not config:
        config = ReferralConfig(id=1, mode="both", referrer_reward_rs=50, referred_reward_rs=20, is_active=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return {
        "status": "success",
        "total": total,
        "config": {
            "mode": config.mode,
            "referrer_reward_rs": config.referrer_reward_rs,
            "referred_reward_rs": config.referred_reward_rs,
            "is_active": bool(config.is_active),
        },
        "leaders": [
            {
                "id": u.id,
                "full_name": u.full_name,
                "phone_number": u.phone_number,
                "role": u.role,
                "referral_code": u.referral_code,
                "referral_points": u.referral_points,
                "referral_earnings": getattr(u, "referral_earnings", 0) or 0,
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


@router.put("/referrals/config")
def admin_update_referral_config(
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Set referral Rs rewards: mode both|referrer_only + amounts."""
    from fastapi import HTTPException
    from app.db.models import ReferralConfig
    config = db.query(ReferralConfig).filter(ReferralConfig.id == 1).first()
    if not config:
        config = ReferralConfig(id=1, mode="both", referrer_reward_rs=50, referred_reward_rs=20, is_active=1)
        db.add(config)
    if "mode" in payload and payload["mode"] is not None:
        mode = str(payload["mode"]).strip().lower()
        if mode not in ("both", "referrer_only"):
            raise HTTPException(status_code=400, detail="mode must be both or referrer_only")
        config.mode = mode
    if "referrer_reward_rs" in payload and payload["referrer_reward_rs"] is not None:
        try:
            config.referrer_reward_rs = max(0, int(payload["referrer_reward_rs"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="referrer_reward_rs must be a number")
    if "referred_reward_rs" in payload and payload["referred_reward_rs"] is not None:
        try:
            config.referred_reward_rs = max(0, int(payload["referred_reward_rs"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="referred_reward_rs must be a number")
    if "is_active" in payload and payload["is_active"] is not None:
        config.is_active = 1 if payload["is_active"] else 0
    db.commit()
    db.refresh(config)
    return {
        "status": "success",
        "config": {
            "mode": config.mode,
            "referrer_reward_rs": config.referrer_reward_rs,
            "referred_reward_rs": config.referred_reward_rs,
            "is_active": bool(config.is_active),
        },
    }


@router.get("/discounts")
def admin_list_discounts(
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """List all discount codes (all customers or specific customers)."""
    from app.db.models import Discount
    rows = db.query(Discount).order_by(Discount.id.desc()).all()
    return {
        "status": "success",
        "discounts": [
            {
                "id": d.id,
                "code": d.code,
                "discount_type": d.discount_type,
                "value": d.value,
                "scope": d.scope,
                "user_ids": d.user_ids,
                "min_amount": d.min_amount,
                "max_uses": d.max_uses,
                "used_count": d.used_count,
                "is_active": bool(d.is_active),
                "valid_from": d.valid_from,
                "valid_to": d.valid_to,
            }
            for d in rows
        ],
    }


@router.post("/discounts")
def admin_create_discount(
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Create a discount code for all customers or specific user ids."""
    import json
    from fastapi import HTTPException
    from app.db.models import Discount
    code = str(payload.get("code") or "").strip().upper()
    if not code or len(code) < 3:
        raise HTTPException(status_code=400, detail="Code must be at least 3 characters")
    if db.query(Discount).filter(Discount.code == code).first():
        raise HTTPException(status_code=400, detail="Discount code already exists")
    dtype = str(payload.get("discount_type") or "flat").strip().lower()
    if dtype not in ("flat", "percent"):
        raise HTTPException(status_code=400, detail="discount_type must be flat or percent")
    try:
        value = int(payload.get("value") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="value must be a number")
    if dtype == "percent" and (value < 1 or value > 100):
        raise HTTPException(status_code=400, detail="percent value must be 1-100")
    if value < 0:
        raise HTTPException(status_code=400, detail="value must be >= 0")
    scope = str(payload.get("scope") or "all").strip().lower()
    if scope not in ("all", "users"):
        raise HTTPException(status_code=400, detail="scope must be all or users")
    user_ids_raw = None
    if scope == "users":
        ids = payload.get("user_ids") or payload.get("users") or []
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except Exception:
                ids = [x.strip() for x in ids.split(",") if x.strip()]
        try:
            ids = [int(x) for x in (ids or [])]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="user_ids must be a list of user ids")
        if not ids:
            raise HTTPException(status_code=400, detail="Add at least one customer for scope users")
        user_ids_raw = json.dumps(ids)
    d = Discount(
        code=code,
        discount_type=dtype,
        value=value,
        scope=scope,
        user_ids=user_ids_raw,
        min_amount=int(payload.get("min_amount") or 0),
        max_uses=payload.get("max_uses"),
        used_count=0,
        is_active=1 if payload.get("is_active", True) else 0,
        valid_from=payload.get("valid_from"),
        valid_to=payload.get("valid_to"),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return {"status": "success", "discount": {"id": d.id, "code": d.code}}


@router.put("/discounts/{discount_id}")
def admin_update_discount(
    discount_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Update a discount code."""
    import json
    from fastapi import HTTPException
    from app.db.models import Discount
    d = db.query(Discount).filter(Discount.id == discount_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Discount not found")
    if "discount_type" in payload and payload["discount_type"] is not None:
        dtype = str(payload["discount_type"]).strip().lower()
        if dtype not in ("flat", "percent"):
            raise HTTPException(status_code=400, detail="discount_type must be flat or percent")
        d.discount_type = dtype
    if "value" in payload and payload["value"] is not None:
        try:
            d.value = max(0, int(payload["value"]))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="value must be a number")
    if "scope" in payload and payload["scope"] is not None:
        scope = str(payload["scope"]).strip().lower()
        if scope not in ("all", "users"):
            raise HTTPException(status_code=400, detail="scope must be all or users")
        d.scope = scope
    if "user_ids" in payload or "users" in payload:
        ids = payload.get("user_ids", payload.get("users"))
        if isinstance(ids, str):
            try:
                ids = json.loads(ids)
            except Exception:
                ids = [x.strip() for x in ids.split(",") if x.strip()]
        try:
            ids = [int(x) for x in (ids or [])]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="user_ids must be a list of user ids")
        d.user_ids = json.dumps(ids) if ids else None
    for key in ("min_amount", "max_uses"):
        if key in payload:
            val = payload[key]
            if val is None or val == "":
                setattr(d, key, None if key == "max_uses" else 0)
            else:
                try:
                    setattr(d, key, int(val))
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail=f"{key} must be a number")
    if "is_active" in payload and payload["is_active"] is not None:
        d.is_active = 1 if payload["is_active"] else 0
    if "valid_from" in payload:
        d.valid_from = payload["valid_from"] or None
    if "valid_to" in payload:
        d.valid_to = payload["valid_to"] or None
    db.commit()
    db.refresh(d)
    return {"status": "success", "discount": {"id": d.id, "code": d.code}}


@router.delete("/discounts/{discount_id}")
def admin_delete_discount(
    discount_id: int,
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Delete a discount code."""
    from fastapi import HTTPException
    from app.db.models import Discount
    d = db.query(Discount).filter(Discount.id == discount_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Discount not found")
    db.delete(d)
    db.commit()
    return {"status": "success", "message": "Discount deleted"}


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


@router.get("/reports/summary")
def admin_reports_summary(
    date_from: str | None = Query(default=None, alias="from"),
    date_to: str | None = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Business overview: bookings, revenue, repeat rate, bookings by day."""
    from datetime import datetime
    from sqlalchemy import func as _func
    q = db.query(Booking)
    if date_from:
        q = q.filter(Booking.date >= date_from)
    if date_to:
        q = q.filter(Booking.date <= date_to)
    rows = q.all()
    total_bookings = len(rows)
    completed = [b for b in rows if b.status == "completed"]
    revenue = 0
    for b in completed:
        try:
            revenue += int(float(str(b.amount or "0").strip() or 0))
        except (TypeError, ValueError):
            pass
    per_customer: dict[int, int] = {}
    for b in rows:
        per_customer[b.customer_id] = per_customer.get(b.customer_id, 0) + 1
    ever_active = len(per_customer)
    repeat = sum(1 for v in per_customer.values() if v > 1)
    repeat_rate = round((repeat / ever_active * 100) if ever_active else 0, 1)
    by_day: dict[str, int] = {}
    rev_by_day: dict[str, int] = {}
    for b in rows:
        day = str(b.date or "")[:10]
        if not day:
            continue
        by_day[day] = by_day.get(day, 0) + 1
        if b.status == "completed":
            try:
                rev_by_day[day] = rev_by_day.get(day, 0) + int(float(str(b.amount or "0").strip() or 0))
            except (TypeError, ValueError):
                pass
    daily = [{"date": d, "bookings": by_day[d], "revenue": rev_by_day.get(d, 0)} for d in sorted(by_day)]
    return {
        "status": "success",
        "total_bookings": total_bookings,
        "completed_bookings": len(completed),
        "total_revenue": revenue,
        "ever_active_customers": ever_active,
        "repeat_customers": repeat,
        "repeat_rate": repeat_rate,
        "daily": daily,
    }


@router.get("/reports/churn")
def admin_reports_churn(
    days: int = Query(default=60, ge=7, le=365),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Customer churn: active vs churned over the last N days + churn rate."""
    from datetime import datetime, timedelta
    today = datetime.now().date()
    cutoff = today - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    rows = db.query(Booking.customer_id, Booking.date).all()
    ever_active: set[int] = set()
    recent_active: set[int] = set()
    for cid, d in rows:
        ever_active.add(cid)
        try:
            if str(d) >= cutoff_str:
                recent_active.add(cid)
        except Exception:
            pass
    # Churned = had a booking before cutoff but none since.
    before_cutoff: set[int] = set()
    for cid, d in rows:
        try:
            if str(d) < cutoff_str:
                before_cutoff.add(cid)
        except Exception:
            pass
    churned = before_cutoff - recent_active
    ever_count = len(ever_active)
    churn_rate = round((len(churned) / ever_count * 100) if ever_count else 0, 1)
    return {
        "status": "success",
        "days": days,
        "cutoff": cutoff_str,
        "ever_active_customers": ever_count,
        "active_customers": len(recent_active),
        "churned_customers": len(churned),
        "churn_rate": churn_rate,
        "churned_ids": sorted(churned)[:200],
    }
