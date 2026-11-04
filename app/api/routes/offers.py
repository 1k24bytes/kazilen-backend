from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.database import get_db
from app.schemas.marketing import OfferCreate, OfferUpdate
from app.services.marketing_service import MarketingService

router = APIRouter()


@router.get("")
@router.get("/")
def public_offers(db: Session = Depends(get_db)):
    """Public homepage offers / referral banners."""
    return MarketingService.public_offers(db)


@router.get("/admin/list")
def admin_offers(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.admin_offers(db)


@router.post("/admin")
def admin_create_offer(payload: OfferCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.create_offer(payload.dict(), db)


@router.put("/admin/{offer_id}")
def admin_update_offer(offer_id: int, payload: OfferUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.update_offer(offer_id, payload.dict(exclude_unset=True), db)


@router.delete("/admin/{offer_id}")
def admin_delete_offer(offer_id: int, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return MarketingService.delete_offer(offer_id, db)
