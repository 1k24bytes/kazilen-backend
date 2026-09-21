import json
from datetime import datetime, timezone
from typing import Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.db.models import MembershipPlan, User, BookingReview, Address


def _time_left_label(expires_at) -> str:
    try:
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)
        exp = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at.astimezone(timezone.utc)
        delta = exp - datetime.now(timezone.utc)
        days = max(0, delta.days)
        if days == 0:
            hours = max(0, int(delta.total_seconds() // 3600))
            return f"{hours}h left" if hours else "Expires today"
        return f"{days} day{'s' if days != 1 else ''} left"
    except Exception:
        return "Active"


def _plan_price(db: Session, plan_id) -> str:
    if not plan_id:
        return ""
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id).first()
    return str(plan.price) if plan else ""


def _plan_days(db: Session, plan_id) -> int | None:
    if not plan_id:
        return None
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id).first()
    return plan.duration_days if plan else None

class WorkerService:
    @staticmethod
    def get_available_workers(
        service_id: Optional[str] = None,
        sub_category: Optional[str] = None,
        db: Session = None,
        hide_exhausted: bool = False,
    ) -> dict:
        target_service = service_id or sub_category
        workers = (
            db.query(User)
            .filter(
                User.role == "worker",
                User.is_online != 0,
                # Only admin-approved workers are visible in the marketplace.
                User.verification_status == "approved",
            )
            .all()
            if db else []
        )
        filtered = []

        for w in workers:
            enabled = True
            parsed_services = []
            if w.offered_services:
                try:
                    parsed = json.loads(w.offered_services)
                    if isinstance(parsed, list):
                        parsed_services = parsed
                    else:
                        parsed_services = str(w.offered_services).split(",")
                except Exception:
                    parsed_services = str(w.offered_services).split(",")

            if target_service and parsed_services:
                extracted_ids = []
                for item in parsed_services:
                    if isinstance(item, dict) and "id" in item:
                        extracted_ids.append(str(item["id"]))
                    else:
                        extracted_ids.append(str(item))
                enabled = target_service in extracted_ids

            if enabled:
                if hide_exhausted and db is not None:
                    try:
                        from app.services.membership_service import MembershipService
                        if MembershipService.worker_quota(w.id, db)["quota_exhausted"]:
                            continue
                    except Exception:
                        pass
                # Calculate real rating and review count from BookingReview table
                reviews = db.query(BookingReview).filter(BookingReview.reviewee_id == w.id).all() if db else []
                if reviews:
                    total_score = sum(r.rating for r in reviews if r.rating is not None)
                    avg_rating = round(total_score / len(reviews), 1)
                    reviews_count = len(reviews)
                else:
                    avg_rating = None
                    reviews_count = 0

                # Resolve worker operational locality
                worker_addr = (
                    db.query(Address)
                    .filter(Address.user_id == w.id)
                    .order_by(desc(Address.is_default), desc(Address.id))
                    .first()
                    if db else None
                )
                if worker_addr and worker_addr.area:
                    locality_val = f"{worker_addr.area}, {worker_addr.city or 'Nagpur'}"
                elif worker_addr and worker_addr.full_address:
                    locality_val = worker_addr.full_address
                else:
                    locality_val = "Dharampeth, Nagpur"

                filtered.append({
                    "id": w.id,
                    "full_name": w.full_name or "Verified Partner",
                    "phone_number": w.phone_number,
                    "profile_photo": w.profile_photo,
                    "rating": avg_rating,
                    "reviews_count": reviews_count,
                    "locality": locality_val,
                    "eta": "Arrives in 30 mins",
                    "jobs_completed": f"{reviews_count}+" if reviews_count > 0 else "0",
                    "offered_services": parsed_services
                })

        return {"status": "success", "workers": filtered}

    @staticmethod
    def get_worker_dashboard(current_user: User, db: Session) -> dict:
        from app.db.models import Booking
        completed_bookings = db.query(Booking).filter(
            Booking.worker_id == current_user.id,
            Booking.status == "completed"
        ).all()

        total_earnings = 0
        for b in completed_bookings:
            try:
                total_earnings += float(b.amount) if b.amount else 0
            except (ValueError, TypeError):
                pass

        completed_count = len(completed_bookings)

        # Calculate ratings and recent reviews for dashboard
        reviews = (
            db.query(BookingReview, User)
            .join(User, User.id == BookingReview.reviewer_id)
            .filter(BookingReview.reviewee_id == current_user.id)
            .order_by(BookingReview.created_at.desc())
            .all()
        )

        if reviews:
            total_score = sum(r.rating for r, _ in reviews if r.rating is not None)
            avg_rating = round(total_score / len(reviews), 1)
            reviews_count = len(reviews)
        else:
            avg_rating = None
            reviews_count = 0

        recent_reviews = []
        for r, reviewer in reviews[:3]:
            recent_reviews.append({
                "id": r.id,
                "booking_id": r.booking_id,
                "reviewer_name": reviewer.full_name or "Verified Customer",
                "rating": r.rating,
                "description": r.description,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })

        # Count active services
        active_services_count = 0
        if current_user.offered_services:
            try:
                parsed = json.loads(current_user.offered_services)
                if isinstance(parsed, list):
                    active_services_count = len(parsed)
            except Exception:
                pass

        from app.services.membership_service import MembershipService
        quota = MembershipService.worker_quota(current_user.id, db)
        sub = quota.get("subscription") or {}
        if quota.get("subscribed"):
            plan_completed = quota.get("plan_used", 0)
            plan_limit = quota.get("plan_limit")
            plan_name = quota.get("plan_name") or "Plan"
            expires_at = sub.get("expires_at")
            time_left = _time_left_label(expires_at) if expires_at else "Active"
            plan_block = {
                "completed": plan_completed,
                "totalLabel": str(plan_limit) if plan_limit is not None else "∞",
                "price": str(sub.get("plan_name") and _plan_price(db, sub.get("plan_id")) or ""),
                "name": plan_name,
                "timeLeft": time_left,
            }
            recharge_block = {
                "price": str(_plan_price(db, sub.get("plan_id")) or ""),
                "validityDays": _plan_days(db, sub.get("plan_id")) or 30,
                "orderType": plan_name,
            }
        else:
            plan_block = {
                "completed": quota.get("free_used", 0),
                "totalLabel": str(quota.get("free_limit", 2)),
                "price": "Free trial",
                "name": "Free trial",
                "timeLeft": f"{quota.get('free_remaining', 0)} free left",
            }
            recharge_block = {
                "price": "",
                "validityDays": 30,
                "orderType": "Membership plans",
            }

        return {
            "status": "success",
            "verification_status": current_user.verification_status or "approved",
            "profile_photo": current_user.profile_photo,
            "progress": {
                "today": {
                    "earnings": total_earnings,
                    "hours": f"{completed_count * 2}:00 hrs",
                    "orders": completed_count
                },
                "week": {
                    "earnings": total_earnings,
                    "hours": f"{completed_count * 2}:00 hrs",
                    "orders": completed_count
                }
            },
            "rating": avg_rating,
            "reviews_count": reviews_count,
            "recent_reviews": recent_reviews,
            "active_services_count": active_services_count,
            "membership": quota,
            "plan": plan_block,
            "recharge": recharge_block,
        }

