from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.database import get_db
from app.schemas.marketing import PopupCreate, PopupUpdate
from app.services.marketing_service import MarketingService

router = APIRouter()


@router.get("/active")
def public_active_popup(db: Session = Depends(get_db)):
    """Public timed popup for homepage (5s modal). Returns null when none active."""
    return MarketingService.active_popup(db)


@router.get("/admin/list")
def admin_popups(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.admin_popups(db)


@router.post("/admin")
def admin_create_popup(payload: PopupCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.create_popup(payload.dict(), db)


@router.put("/admin/{popup_id}")
def admin_update_popup(popup_id: int, payload: PopupUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.update_popup(popup_id, payload.dict(exclude_unset=True), db)


@router.delete("/admin/{popup_id}")
def admin_delete_popup(popup_id: int, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.delete_popup(popup_id, db)
