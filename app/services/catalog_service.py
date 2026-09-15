import json
import os
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.db.models import Category, SubCategory
from app.schemas.catalog import (
    CategoryCreate,
    CategoryUpdate,
    SubCategoryCreate,
    SubCategoryUpdate,
)


def _to_bool(value) -> bool:
    return bool(value)


def _category_to_dict(category: Category, sub_count: int = 0) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "description": category.description,
        "image": category.image,
        "is_active": _to_bool(category.is_active),
        "sort_order": category.sort_order or 0,
        "sub_categories_count": sub_count,
    }


def _sub_to_dict(sub: SubCategory) -> dict:
    return {
        "id": sub.id,
        "category_id": sub.category_id,
        "label": sub.label,
        "description": sub.description,
        "image": sub.image,
        "tag": sub.tag,
        "price_type": sub.price_type or "fixed",
        "fixed_price": sub.fixed_price,
        "price_per_hour": sub.price_per_hour,
        "is_active": _to_bool(sub.is_active),
        "sort_order": sub.sort_order or 0,
    }


class CatalogService:
    @staticmethod
    def _services_json_path() -> str | None:
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(here, "..", "..", "..", "frontend-customer", "app", "data", "services.json"),
            os.path.join(here, "..", "..", "..", "worker-frontend", "app", "data", "services.json"),
            os.path.join(os.getcwd(), "frontend-customer", "app", "data", "services.json"),
            os.path.join(os.getcwd(), "worker-frontend", "app", "data", "services.json"),
        ]
        for candidate in candidates:
            normalized = os.path.normpath(candidate)
            if os.path.exists(normalized):
                return normalized
        return None

    @staticmethod
    def _read_seed_file() -> dict:
        path = CatalogService._services_json_path()
        if not path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="services.json seed file not found",
            )
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def sync_from_seed(db: Session) -> dict:
        seed = CatalogService._read_seed_file()
        categories = seed.get("categories", []) or []
        sub_categories = seed.get("subCategories", seed.get("sub_categories", [])) or []

        cat_count = 0
        for order, item in enumerate(categories):
            cat_id = str(item.get("id", "")).strip()
            if not cat_id:
                continue
            existing = db.query(Category).filter(Category.id == cat_id).first()
            if existing:
                existing.name = item.get("name") or existing.name
                existing.description = item.get("desc") or item.get("description") or existing.description
                existing.image = item.get("image") or existing.image
                if existing.is_active is None:
                    existing.is_active = 1
            else:
                db.add(Category(
                    id=cat_id,
                    name=item.get("name") or cat_id,
                    description=item.get("desc") or item.get("description"),
                    image=item.get("image"),
                    is_active=1,
                    sort_order=order,
                ))
            cat_count += 1

        sub_count = 0
        for order, item in enumerate(sub_categories):
            sub_id = str(item.get("id", "")).strip()
            category_id = str(item.get("categoryId") or item.get("category_id") or "").strip()
            if not sub_id or not category_id:
                continue
            price_type = item.get("default_price_type") or item.get("price_type") or "fixed"
            if price_type not in ("fixed", "hourly"):
                price_type = "fixed"
            existing = db.query(SubCategory).filter(SubCategory.id == sub_id).first()
            if existing:
                existing.category_id = category_id
                existing.label = item.get("label") or existing.label
                existing.description = item.get("default_description") or item.get("description") or existing.description
                existing.image = item.get("image") or existing.image
                existing.tag = item.get("tag") or existing.tag
                if item.get("default_fixed_price") is not None:
                    existing.fixed_price = item.get("default_fixed_price")
                if item.get("default_price_per_hour") is not None:
                    existing.price_per_hour = item.get("default_price_per_hour")
                existing.price_type = price_type
            else:
                db.add(SubCategory(
                    id=sub_id,
                    category_id=category_id,
                    label=item.get("label") or sub_id,
                    description=item.get("default_description") or item.get("description"),
                    image=item.get("image"),
                    tag=item.get("tag"),
                    price_type=price_type,
                    fixed_price=item.get("default_fixed_price"),
                    price_per_hour=item.get("default_price_per_hour"),
                    is_active=1,
                    sort_order=order,
                ))
            sub_count += 1

        db.commit()
        return {
            "status": "success",
            "categories_upserted": cat_count,
            "sub_categories_upserted": sub_count,
            "synced_at": datetime.now(timezone.utc),
        }

    @staticmethod
    def get_public_catalog(db: Session) -> dict:
        categories = (
            db.query(Category)
            .filter(Category.is_active == 1)
            .order_by(Category.sort_order.asc(), Category.id.asc())
            .all()
        )
        subs = (
            db.query(SubCategory)
            .filter(SubCategory.is_active == 1)
            .order_by(SubCategory.sort_order.asc(), SubCategory.id.asc())
            .all()
        )
        if not categories and not subs:
            seed = None
            try:
                seed = CatalogService._read_seed_file()
            except HTTPException:
                seed = None
            if seed:
                categories_out = [
                    {
                        "id": str(c.get("id")),
                        "name": c.get("name") or str(c.get("id")),
                        "description": c.get("desc") or c.get("description"),
                        "image": c.get("image"),
                        "is_active": True,
                        "sort_order": idx,
                        "sub_categories_count": 0,
                    }
                    for idx, c in enumerate(seed.get("categories", []) or [])
                ]
                subs_out = [
                    {
                        "id": str(s.get("id")),
                        "category_id": str(s.get("categoryId") or s.get("category_id")),
                        "label": s.get("label") or str(s.get("id")),
                        "description": s.get("default_description") or s.get("description"),
                        "image": s.get("image"),
                        "tag": s.get("tag"),
                        "price_type": s.get("default_price_type") or "fixed",
                        "fixed_price": s.get("default_fixed_price"),
                        "price_per_hour": s.get("default_price_per_hour"),
                        "is_active": True,
                        "sort_order": idx,
                    }
                    for idx, s in enumerate(seed.get("subCategories", seed.get("sub_categories", [])) or [])
                ]
                return {"categories": categories_out, "sub_categories": subs_out}
        counts: dict[str, int] = {}
        for sub in subs:
            counts[sub.category_id] = counts.get(sub.category_id, 0) + 1
        return {
            "categories": [_category_to_dict(c, counts.get(c.id, 0)) for c in categories],
            "sub_categories": [_sub_to_dict(s) for s in subs],
        }

    @staticmethod
    def list_categories(db: Session, include_inactive: bool = True) -> list:
        query = db.query(Category).order_by(Category.sort_order.asc(), Category.id.asc())
        if not include_inactive:
            query = query.filter(Category.is_active == 1)
        categories = query.all()
        counts: dict[str, int] = {}
        for (category_id,) in db.query(SubCategory.category_id).all():
            counts[category_id] = counts.get(category_id, 0) + 1
        return [_category_to_dict(c, counts.get(c.id, 0)) for c in categories]

    @staticmethod
    def create_category(db: Session, payload: CategoryCreate) -> dict:
        if db.query(Category).filter(Category.id == payload.id).first():
            raise HTTPException(status_code=409, detail="Category id already exists")
        category = Category(
            id=payload.id,
            name=payload.name,
            description=payload.description,
            image=payload.image,
            is_active=1 if payload.is_active else 0,
            sort_order=payload.sort_order or 0,
        )
        db.add(category)
        db.commit()
        db.refresh(category)
        return _category_to_dict(category, 0)

    @staticmethod
    def update_category(db: Session, category_id: str, payload: CategoryUpdate) -> dict:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
        data = payload.model_dump(exclude_unset=True)
        if "is_active" in data:
            category.is_active = 1 if data.pop("is_active") else 0
        for key, value in data.items():
            setattr(category, key, value)
        db.commit()
        db.refresh(category)
        count = db.query(SubCategory).filter(SubCategory.category_id == category.id).count()
        return _category_to_dict(category, count)

    @staticmethod
    def delete_category(db: Session, category_id: str) -> dict:
        category = db.query(Category).filter(Category.id == category_id).first()
        if not category:
            raise HTTPException(status_code=404, detail="Category not found")
        in_use = db.query(SubCategory).filter(SubCategory.category_id == category_id).count()
        if in_use:
            raise HTTPException(
                status_code=409,
                detail=f"Category has {in_use} sub-categories. Move or delete them first.",
            )
        db.delete(category)
        db.commit()
        return {"status": "success", "message": "Category deleted"}

    @staticmethod
    def list_sub_categories(db: Session, category_id: str | None = None, include_inactive: bool = True) -> list:
        query = db.query(SubCategory).order_by(SubCategory.sort_order.asc(), SubCategory.id.asc())
        if category_id:
            query = query.filter(SubCategory.category_id == category_id)
        if not include_inactive:
            query = query.filter(SubCategory.is_active == 1)
        return [_sub_to_dict(s) for s in query.all()]

    @staticmethod
    def create_sub_category(db: Session, payload: SubCategoryCreate) -> dict:
        if db.query(SubCategory).filter(SubCategory.id == payload.id).first():
            raise HTTPException(status_code=409, detail="Sub-category id already exists")
        if not db.query(Category).filter(Category.id == payload.category_id).first():
            raise HTTPException(status_code=404, detail="Parent category not found")
        sub = SubCategory(
            id=payload.id,
            category_id=payload.category_id,
            label=payload.label,
            description=payload.description,
            image=payload.image,
            tag=payload.tag,
            price_type=payload.price_type or "fixed",
            fixed_price=payload.fixed_price,
            price_per_hour=payload.price_per_hour,
            is_active=1 if payload.is_active else 0,
            sort_order=payload.sort_order or 0,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        return _sub_to_dict(sub)

    @staticmethod
    def update_sub_category(db: Session, sub_id: str, payload: SubCategoryUpdate) -> dict:
        sub = db.query(SubCategory).filter(SubCategory.id == sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Sub-category not found")
        data = payload.model_dump(exclude_unset=True)
        if "is_active" in data:
            sub.is_active = 1 if data.pop("is_active") else 0
        if "category_id" in data and data["category_id"]:
            if not db.query(Category).filter(Category.id == data["category_id"]).first():
                raise HTTPException(status_code=404, detail="Parent category not found")
        for key, value in data.items():
            setattr(sub, key, value)
        db.commit()
        db.refresh(sub)
        return _sub_to_dict(sub)

    @staticmethod
    def delete_sub_category(db: Session, sub_id: str) -> dict:
        sub = db.query(SubCategory).filter(SubCategory.id == sub_id).first()
        if not sub:
            raise HTTPException(status_code=404, detail="Sub-category not found")
        db.delete(sub)
        db.commit()
        return {"status": "success", "message": "Sub-category deleted"}
