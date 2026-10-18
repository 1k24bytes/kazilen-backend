from fastapi import APIRouter, Depends, File, Query, UploadFile
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


@router.post("/admin/catalog/upload")
def admin_upload_catalog_image(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Accepts JPEG/PNG/WebP from PC, converts to WebP, stores via R2 (or local /static fallback)."""
    import io
    import os
    import uuid
    from fastapi import HTTPException, status

    ALLOWED = {"image/jpeg", "image/png", "image/webp"}
    if not file or not file.content_type or file.content_type not in ALLOWED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only JPEG, PNG or WebP images are allowed.")
    contents = file.file.read()
    if not contents or len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image must be non-empty and under 5 MB.")
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(contents))
        if img.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            try:
                bg.paste(img, mask=img.split()[-1])
                img = bg
            except Exception:
                img = img.convert("RGB")
        else:
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=82, method=6)
        webp_bytes = out.getvalue()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid image file: {e}")

    # Prefer R2 when configured (same pattern as worker photos).
    try:
        from app.services.r2_service import R2Service
        from app.core.config import settings as _settings
        if _settings.R2_PUBLIC_BUCKET_NAME and _settings.R2_PUBLIC_URL:
            result = R2Service.upload_public_photo(webp_bytes, "upload.webp", "image/webp", folder="catalog")
            return {"status": "success", "url": result["url"], "publicId": result["key"]}
    except HTTPException:
        pass
    except Exception:
        pass

    # Local fallback: backend/static/catalog/<uuid>.webp served at /static/...
    try:
        base_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "static", "catalog")
        os.makedirs(base_dir, exist_ok=True)
        fname = f"{uuid.uuid4()}.webp"
        with open(os.path.join(base_dir, fname), "wb") as fh:
            fh.write(webp_bytes)
        return {"status": "success", "url": f"/static/catalog/{fname}", "publicId": f"static/catalog/{fname}"}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Upload failed: {e}")
