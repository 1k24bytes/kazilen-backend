from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin
from app.db.database import get_db
from app.schemas.catalog import (
    CatalogResponse,
    CatalogSyncResponse,
    CategoryCreate,
    CategoryUpdate,
    SubCategoryCreate,
    SubCategoryUpdate,
)
from app.services.catalog_service import CatalogService
from app.services.membership_service import MembershipService

router = APIRouter()


@router.get("/catalog", response_model=CatalogResponse)
def public_catalog(db: Session = Depends(get_db)):
    return CatalogService.get_public_catalog(db)


@router.get("/membership/plans")
def public_membership_plans(db: Session = Depends(get_db)):
    plans = MembershipService.list_plans(db, include_inactive=False, public_only=True)
    if not plans:
        plans = MembershipService.ensure_seed_plans(db)
        plans = [p for p in plans if p.get("is_active")]
    return {"status": "success", "plans": plans}


@router.get("/admin/catalog", response_model=CatalogResponse)
def admin_catalog(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return {
        "categories": CatalogService.list_categories(db),
        "sub_categories": CatalogService.list_sub_categories(db),
    }


@router.post("/admin/catalog/sync", response_model=CatalogSyncResponse)
def admin_catalog_sync(db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.sync_from_seed(db)


@router.post("/admin/categories")
def admin_create_category(payload: CategoryCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.create_category(db, payload)


@router.put("/admin/categories/{category_id}")
def admin_update_category(category_id: str, payload: CategoryUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.update_category(db, category_id, payload)


@router.delete("/admin/categories/{category_id}")
def admin_delete_category(category_id: str, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.delete_category(db, category_id)


@router.get("/admin/sub-categories")
def admin_list_sub_categories(
    category_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    subs = CatalogService.list_sub_categories(db, category_id=category_id)
    return {"status": "success", "total": len(subs), "sub_categories": subs}


@router.post("/admin/sub-categories")
def admin_create_sub_category(payload: SubCategoryCreate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.create_sub_category(db, payload)


@router.put("/admin/sub-categories/{sub_id}")
def admin_update_sub_category(sub_id: str, payload: SubCategoryUpdate, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.update_sub_category(db, sub_id, payload)


@router.delete("/admin/sub-categories/{sub_id}")
def admin_delete_sub_category(sub_id: str, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return CatalogService.delete_sub_category(db, sub_id)
