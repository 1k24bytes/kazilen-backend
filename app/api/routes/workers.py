from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.services.worker_service import WorkerService
from app.services.review_service import get_worker_reviews

router = APIRouter()

@router.get("/available")
@router.get("")
def get_available_workers(
    service_id: Optional[str] = None,
    sub_category: Optional[str] = None,
    hide_exhausted: bool = False,
    db: Session = Depends(get_db)
):
    """Retrieves list of available workers filtered optionally by service or sub-category ID."""
    return WorkerService.get_available_workers(service_id=service_id, sub_category=sub_category, db=db, hide_exhausted=hide_exhausted)

@router.get("/dashboard")
@router.get("/worker/dashboard")
def get_worker_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieves worker dashboard data (earnings, hours, completed jobs, active plan)."""
    return WorkerService.get_worker_dashboard(current_user, db)

@router.get("/{worker_id}/reviews")
def get_worker_reviews_endpoint(
    worker_id: int,
    db: Session = Depends(get_db)
):
    """Public endpoint returning all reviews received by a worker (customer marketplace view)."""
    return get_worker_reviews(worker_id, db)

