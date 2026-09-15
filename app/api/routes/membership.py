from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.db.database import get_db
from app.schemas.membership import (
    MembershipPlanCreate,
    MembershipPlanUpdate,
    MembershipSubscriptionCreate,
)
from app.services.membership_service import MembershipService

router = APIRouter()


@router.get("/plans")
def worker_plans(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    plans = MembershipService.list_plans(db, include_inactive=False, public_only=True)
    if not plans:
        plans = MembershipService.ensure_seed_plans(db)
        plans = [p for p in plans if p.get("is_active")]
    return {"status": "success", "plans": plans}


@router.get("/me")
def my_subscription(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return MembershipService.worker_subscription(current_user.id, db)


@router.get("/quota")
def my_quota(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Live quota for the worker: free tier + active plan usage."""
    return {"status": "success", "quota": MembershipService.worker_quota(current_user.id, db)}


@router.post("/subscribe")
def self_subscribe(plan_id: int = Query(...), db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Worker subscribes to a plan (mock payment — no gateway yet)."""
    return MembershipService.self_subscribe(current_user.id, plan_id, db)


@router.get("/admin/plans")
def admin_plans(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    plans = MembershipService.ensure_seed_plans(db)
    return {"status": "success", "total": len(plans), "plans": plans}


@router.post("/admin/plans")
def admin_create_plan(payload: MembershipPlanCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MembershipService.create_plan(db, payload)


@router.put("/admin/plans/{plan_id}")
def admin_update_plan(plan_id: int, payload: MembershipPlanUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MembershipService.update_plan(db, plan_id, payload)


@router.delete("/admin/plans/{plan_id}")
def admin_delete_plan(plan_id: int, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MembershipService.delete_plan(db, plan_id)


@router.get("/admin/subscriptions")
def admin_subscriptions(
    worker_id: int | None = Query(default=None),
    plan_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    return MembershipService.list_subscriptions(db, worker_id=worker_id, plan_id=plan_id, status_filter=status, limit=limit, offset=offset)


@router.post("/admin/subscriptions")
def admin_subscribe(payload: MembershipSubscriptionCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MembershipService.subscribe(db, payload)


@router.put("/admin/subscriptions/{subscription_id}")
def admin_subscription_status(subscription_id: int, status: str = Query(...), db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MembershipService.set_subscription_status(db, subscription_id, status)
