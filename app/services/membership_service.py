import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import MembershipPlan, MembershipSubscription, User
from app.schemas.membership import (
    MembershipPlanCreate,
    MembershipPlanUpdate,
    MembershipSubscriptionCreate,
)


def _features_to_list(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        return [str(raw)]
    return []


def _plan_to_dict(plan: MembershipPlan, subscribers_count: int = 0) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "price": plan.price or 0,
        "duration_days": plan.duration_days or 30,
        "bookings_included": plan.bookings_included,
        "description": plan.description,
        "features": _features_to_list(plan.features),
        "is_active": bool(plan.is_active),
        "subscribers_count": subscribers_count,
    }


def _sub_to_dict(sub: MembershipSubscription, db: Session) -> dict:
    worker = db.query(User).filter(User.id == sub.worker_id).first()
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == sub.plan_id).first()
    return {
        "id": sub.id,
        "worker_id": sub.worker_id,
        "worker_name": worker.full_name if worker else None,
        "plan_id": sub.plan_id,
        "plan_name": plan.name if plan else None,
        "status": sub.status,
        "starts_at": sub.starts_at,
        "expires_at": sub.expires_at,
        "created_at": sub.created_at,
    }


class MembershipService:
    @staticmethod
    def list_plans(db: Session, include_inactive: bool = True, public_only: bool = False) -> list:
        query = db.query(MembershipPlan).order_by(MembershipPlan.price.asc(), MembershipPlan.id.asc())
        if not include_inactive or public_only:
            query = query.filter(MembershipPlan.is_active == 1)
        plans = query.all()
        out = []
        for plan in plans:
            count = db.query(MembershipSubscription).filter(
                MembershipSubscription.plan_id == plan.id,
                MembershipSubscription.status == "active",
            ).count()
            out.append(_plan_to_dict(plan, count))
        return out

    @staticmethod
    def create_plan(db: Session, payload: MembershipPlanCreate) -> dict:
        plan = MembershipPlan(
            name=payload.name.strip(),
            price=payload.price or 0,
            duration_days=payload.duration_days or 30,
            bookings_included=payload.bookings_included,
            description=payload.description,
            features=json.dumps(payload.features or []),
            is_active=1 if payload.is_active else 0,
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return _plan_to_dict(plan, 0)

    @staticmethod
    def update_plan(db: Session, plan_id: int, payload: MembershipPlanUpdate) -> dict:
        plan = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found")
        data = payload.model_dump(exclude_unset=True)
        if "is_active" in data:
            plan.is_active = 1 if data.pop("is_active") else 0
        if "features" in data:
            plan.features = json.dumps(data.pop("features") or [])
        for key, value in data.items():
            setattr(plan, key, value)
        db.commit()
        db.refresh(plan)
        count = db.query(MembershipSubscription).filter(
            MembershipSubscription.plan_id == plan.id,
            MembershipSubscription.status == "active",
        ).count()
        return _plan_to_dict(plan, count)

    @staticmethod
    def delete_plan(db: Session, plan_id: int) -> dict:
        plan = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found")
        active = db.query(MembershipSubscription).filter(
            MembershipSubscription.plan_id == plan_id,
            MembershipSubscription.status == "active",
        ).count()
        if active:
            raise HTTPException(
                status_code=409,
                detail=f"Plan has {active} active subscribers. Cancel them first.",
            )
        db.delete(plan)
        db.commit()
        return {"status": "success", "message": "Membership plan deleted"}

    @staticmethod
    def ensure_seed_plans(db: Session) -> list:
        if db.query(MembershipPlan).count():
            return MembershipService.list_plans(db)
        seeds = [
            {
                "name": "Starter",
                "price": 399,
                "duration_days": 30,
                "bookings_included": 10,
                "description": "Entry plan for new workers with local dispatch.",
                "features": ["10 bookings", "Local dispatch", "Standard support"],
            },
            {
                "name": "Pro",
                "price": 799,
                "duration_days": 30,
                "bookings_included": 30,
                "description": "Higher booking limit with priority dispatch.",
                "features": ["30 bookings", "Priority dispatch", "Priority support"],
            },
        ]
        for seed in seeds:
            db.add(MembershipPlan(
                name=seed["name"],
                price=seed["price"],
                duration_days=seed["duration_days"],
                bookings_included=seed["bookings_included"],
                description=seed["description"],
                features=json.dumps(seed["features"]),
                is_active=1,
            ))
        db.commit()
        return MembershipService.list_plans(db)

    @staticmethod
    def subscribe(db: Session, payload: MembershipSubscriptionCreate) -> dict:
        worker = db.query(User).filter(User.id == payload.worker_id, User.role == "worker").first()
        if not worker:
            raise HTTPException(status_code=404, detail="Worker not found")
        plan = db.query(MembershipPlan).filter(MembershipPlan.id == payload.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found")
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=plan.duration_days or 30)
        sub = MembershipSubscription(
            worker_id=payload.worker_id,
            plan_id=payload.plan_id,
            status=payload.status,
            starts_at=now,
            expires_at=expires,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return _sub_to_dict(sub, db)

    @staticmethod
    def list_subscriptions(db: Session, worker_id: int | None = None, plan_id: int | None = None, status_filter: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        query = db.query(MembershipSubscription).order_by(MembershipSubscription.id.desc())
        if worker_id:
            query = query.filter(MembershipSubscription.worker_id == worker_id)
        if plan_id:
            query = query.filter(MembershipSubscription.plan_id == plan_id)
        if status_filter:
            query = query.filter(MembershipSubscription.status == status_filter)
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
        return {
            "status": "success",
            "total": total,
            "subscriptions": [_sub_to_dict(r, db) for r in rows],
        }

    @staticmethod
    def set_subscription_status(db: Session, subscription_id: int, status_value: str) -> dict:
        if status_value not in ("active", "expired", "cancelled"):
            raise HTTPException(status_code=400, detail="Invalid status")
        sub = db.query(MembershipSubscription).filter(MembershipSubscription.id == subscription_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Subscription not found")
        sub.status = status_value
        db.commit()
        db.refresh(sub)
        return _sub_to_dict(sub, db)

    @staticmethod
    def worker_subscription(worker_id: int, db: Session) -> dict:
        sub = MembershipService.active_subscription(worker_id, db)
        if not sub:
            return {"subscribed": False, "subscription": None}
        return {"subscribed": True, "subscription": _sub_to_dict(sub, db)}

    @staticmethod
    def active_subscription(worker_id: int, db: Session):
        """Returns the live subscription, auto-expiring past-due rows."""
        from datetime import timezone as _tz
        now = datetime.now(_tz.utc)
        subs = (
            db.query(MembershipSubscription)
            .filter(MembershipSubscription.worker_id == worker_id, MembershipSubscription.status == "active")
            .order_by(MembershipSubscription.expires_at.desc())
            .all()
        )
        live = None
        changed = False
        for sub in subs:
            expires = sub.expires_at
            if expires is not None:
                exp = expires.replace(tzinfo=_tz.utc) if expires.tzinfo is None else expires.astimezone(_tz.utc)
                if exp < now:
                    sub.status = "expired"
                    changed = True
                    continue
            if live is None:
                live = sub
        if changed:
            db.commit()
        return live

    @staticmethod
    def used_bookings(worker_id: int, db: Session, since=None) -> int:
        """Bookings consumed against quota (taken + done, never cancelled/pending)."""
        from app.db.models import Booking
        query = db.query(Booking).filter(
            Booking.worker_id == worker_id,
            Booking.status.in_(["accepted", "in_progress", "completed"]),
        )
        if since is not None:
            try:
                from datetime import datetime as _dt
                if isinstance(since, str):
                    since = _dt.fromisoformat(since)
            except Exception:
                since = None
            if since is not None:
                query = query.filter(Booking.created_at >= since)
        return query.count()

    @staticmethod
    def worker_quota(worker_id: int, db: Session) -> dict:
        """Full quota picture for a worker: free tier + active plan window."""
        from app.core.config import settings
        free_limit = int(getattr(settings, "FREE_BOOKINGS", 2) or 2)
        sub = MembershipService.active_subscription(worker_id, db)
        total_used = MembershipService.used_bookings(worker_id, db)
        if sub is None:
            remaining = max(0, free_limit - total_used)
            return {
                "subscribed": False,
                "subscription": None,
                "plan_name": None,
                "free_limit": free_limit,
                "free_used": min(total_used, free_limit),
                "free_remaining": remaining,
                "plan_limit": None,
                "plan_used": 0,
                "plan_remaining": 0,
                "total_used": total_used,
                "total_limit": free_limit,
                "total_remaining": remaining,
                "quota_exhausted": total_used >= free_limit,
            }
        plan = db.query(MembershipPlan).filter(MembershipPlan.id == sub.plan_id).first()
        plan_limit = plan.bookings_included if plan and plan.bookings_included is not None else None
        starts_at = sub.starts_at
        plan_used = MembershipService.used_bookings(worker_id, db, since=starts_at) if starts_at else total_used
        plan_remaining = None if plan_limit is None else max(0, plan_limit - plan_used)
        exhausted = plan_limit is not None and plan_used >= plan_limit
        return {
            "subscribed": True,
            "subscription": _sub_to_dict(sub, db),
            "plan_name": plan.name if plan else None,
            "free_limit": free_limit,
            "free_used": free_limit,
            "free_remaining": 0,
            "plan_limit": plan_limit,
            "plan_used": plan_used,
            "plan_remaining": plan_remaining,
            "total_used": total_used,
            "total_limit": plan_limit,
            "total_remaining": plan_remaining if plan_remaining is not None else 999999,
            "quota_exhausted": exhausted,
        }

    @staticmethod
    def check_booking_allowed(worker_id: int, db: Session) -> dict:
        """Gate for new bookings. Raises 402 when the worker's quota is spent."""
        quota = MembershipService.worker_quota(worker_id, db)
        if quota["quota_exhausted"]:
            if quota["subscribed"]:
                detail = (
                    f"Booking limit reached ({quota['plan_used']}/{quota['plan_limit']} on {quota['plan_name']}). "
                    "Renew the subscription to accept more jobs."
                )
            else:
                detail = (
                    f"Free bookings used ({quota['free_used']}/{quota['free_limit']}). "
                    "Subscribe to a membership plan to accept more jobs."
                )
            raise HTTPException(status_code=402, detail=detail)
        return quota

    @staticmethod
    def self_subscribe(worker_id: int, plan_id: int, db: Session) -> dict:
        """Worker self-subscribes (mock payment). Cancels prior active subs."""
        worker = db.query(User).filter(User.id == worker_id, User.role == "worker").first()
        if not worker:
            raise HTTPException(status_code=404, detail="Worker not found")
        plan = db.query(MembershipPlan).filter(MembershipPlan.id == plan_id, MembershipPlan.is_active == 1).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found or inactive")
        now = datetime.now(timezone.utc)
        old = db.query(MembershipSubscription).filter(
            MembershipSubscription.worker_id == worker_id,
            MembershipSubscription.status == "active",
        ).all()
        for sub in old:
            sub.status = "cancelled"
        expires = now + timedelta(days=plan.duration_days or 30)
        sub = MembershipSubscription(
            worker_id=worker_id,
            plan_id=plan_id,
            status="active",
            starts_at=now,
            expires_at=expires,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return {
            "status": "success",
            "message": f"Subscribed to {plan.name}. Quota resets from now.",
            "subscription": _sub_to_dict(sub, db),
            "quota": MembershipService.worker_quota(worker_id, db),
        }
