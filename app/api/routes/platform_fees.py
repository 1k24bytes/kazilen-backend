from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.database import get_db
from app.schemas.platform_fee import PlatformFeeSlabCreate, PlatformFeeSlabUpdate
from app.services.platform_fee_service import PlatformFeeService

router = APIRouter()


@router.get("/quote")
def fee_quote(amount: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    """Public quote: base + platform fee + total (used on customer checkout)."""
    return PlatformFeeService.quote(db, amount)


@router.get("/admin/fees")
def admin_list_fees(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Admin: list all slabs (active + inactive)."""
    return PlatformFeeService.list_slabs(db)


@router.post("/admin/fees")
def admin_create_fee(payload: PlatformFeeSlabCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Admin: create slab e.g. {min_amount: 0, max_amount: 200, fee_rs: 25}."""
    return PlatformFeeService.create(db, payload.dict())


@router.put("/admin/fees/{slab_id}")
def admin_update_fee(slab_id: int, payload: PlatformFeeSlabUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Admin: update slab bounds / fee / active flag."""
    data = {k: v for k, v in payload.dict().items() if v is not None or k == "max_amount"}
    # Allow explicit null max_amount to mean infinity; pydantic gives None for omitted too.
    # Only pass keys the client sent (exclude_unset) to avoid clearing max accidentally.
    raw = payload.dict(exclude_unset=True)
    return PlatformFeeService.update(db, slab_id, raw)


@router.delete("/admin/fees/{slab_id}")
def admin_delete_fee(slab_id: int, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    """Admin: delete a slab."""
    return PlatformFeeService.delete(db, slab_id)
