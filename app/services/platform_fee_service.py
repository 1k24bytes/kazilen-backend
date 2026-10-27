"""Platform fee slabs — admin-configurable fee on customer bookings.

Example requested by product:
  0-200   -> Rs 25
  201-500 -> Rs 30

Fee is computed server-side from the final base amount (after discount)
so the client can never spoof it. Quote is public; CRUD is admin-only.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import PlatformFeeSlab


def _to_dict(s: PlatformFeeSlab) -> dict:
    return {
        "id": s.id,
        "min_amount": s.min_amount,
        "max_amount": s.max_amount,
        "fee_rs": s.fee_rs,
        "is_active": bool(s.is_active),
        "sort_order": s.sort_order or 0,
    }


def _ensure_seed(db: Session) -> None:
    if db.query(PlatformFeeSlab).count() > 0:
        return
    db.add_all([
        PlatformFeeSlab(min_amount=0, max_amount=200, fee_rs=25, is_active=1, sort_order=0),
        PlatformFeeSlab(min_amount=201, max_amount=500, fee_rs=30, is_active=1, sort_order=1),
        PlatformFeeSlab(min_amount=501, max_amount=None, fee_rs=50, is_active=1, sort_order=2),
    ])
    db.commit()


def _overlaps(db: Session, min_amount: int, max_amount: int | None, exclude_id: int | None = None) -> bool:
    q = db.query(PlatformFeeSlab).filter(PlatformFeeSlab.is_active == 1)
    if exclude_id:
        q = q.filter(PlatformFeeSlab.id != exclude_id)
    for s in q.all():
        s_min = int(s.min_amount or 0)
        s_max = s.max_amount  # None = infinity
        new_max = max_amount  # None = infinity
        # overlap if ranges intersect
        lo = max(s_min, min_amount)
        if s_max is None and new_max is None:
            return True
        if s_max is None:
            if new_max is None or new_max >= s_min:
                # new range reaches into open-ended slab
                if min_amount <= s_min or new_max >= s_min:
                    # check actual intersection: [min_amount,new_max] vs [s_min,inf)
                    if min_amount <= s_min or True:
                        if new_max is None or new_max >= s_min:
                            return True
            continue
        if new_max is None:
            if s_max >= min_amount:
                return True
            continue
        hi = min(s_max, new_max)
        if lo <= hi:
            return True
    return False


class PlatformFeeService:
    @staticmethod
    def list_slabs(db: Session) -> dict:
        _ensure_seed(db)
        rows = (
            db.query(PlatformFeeSlab)
            .order_by(PlatformFeeSlab.sort_order.asc(), PlatformFeeSlab.min_amount.asc())
            .all()
        )
        return {"status": "success", "slabs": [_to_dict(r) for r in rows]}

    @staticmethod
    def quote(db: Session, amount: int) -> dict:
        _ensure_seed(db)
        try:
            base = max(0, int(amount or 0))
        except (TypeError, ValueError):
            base = 0
        rows = (
            db.query(PlatformFeeSlab)
            .filter(PlatformFeeSlab.is_active == 1)
            .order_by(PlatformFeeSlab.min_amount.asc())
            .all()
        )
        fee = 0
        slab_id = None
        for s in rows:
            s_min = int(s.min_amount or 0)
            s_max = s.max_amount
            if base >= s_min and (s_max is None or base <= int(s_max)):
                fee = int(s.fee_rs or 0)
                slab_id = s.id
                break
        return {
            "status": "success",
            "base_amount": base,
            "platform_fee": fee,
            "total_amount": base + fee,
            "slab_id": slab_id,
            "currency": "INR",
        }

    @staticmethod
    def create(db: Session, payload: dict) -> dict:
        try:
            min_amount = int(payload.get("min_amount", 0))
            fee_rs = int(payload.get("fee_rs", 0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="min_amount and fee_rs must be numbers")
        raw_max = payload.get("max_amount")
        max_amount = None
        if raw_max is not None and str(raw_max).strip() != "":
            try:
                max_amount = int(raw_max)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="max_amount must be a number or empty for no limit")
        if min_amount < 0 or fee_rs < 0:
            raise HTTPException(status_code=400, detail="Amounts must be >= 0")
        if max_amount is not None and max_amount < min_amount:
            raise HTTPException(status_code=400, detail="max_amount must be >= min_amount")
        _ensure_seed(db)
        if _overlaps(db, min_amount, max_amount):
            raise HTTPException(status_code=409, detail="Slab overlaps an existing active slab")
        row = PlatformFeeSlab(
            min_amount=min_amount,
            max_amount=max_amount,
            fee_rs=fee_rs,
            is_active=1 if payload.get("is_active", True) else 0,
            sort_order=int(payload.get("sort_order") or 0),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {"status": "success", "slab": _to_dict(row)}

    @staticmethod
    def update(db: Session, slab_id: int, payload: dict) -> dict:
        row = db.query(PlatformFeeSlab).filter(PlatformFeeSlab.id == slab_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Slab not found")
        min_amount = int(row.min_amount or 0)
        max_amount = row.max_amount
        if "min_amount" in payload and payload["min_amount"] is not None:
            try:
                min_amount = int(payload["min_amount"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="min_amount must be a number")
        if "max_amount" in payload:
            raw = payload["max_amount"]
            if raw is None or str(raw).strip() == "":
                max_amount = None
            else:
                try:
                    max_amount = int(raw)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="max_amount must be a number or empty")
        if min_amount < 0:
            raise HTTPException(status_code=400, detail="min_amount must be >= 0")
        if max_amount is not None and max_amount < min_amount:
            raise HTTPException(status_code=400, detail="max_amount must be >= min_amount")
        if _overlaps(db, min_amount, max_amount, exclude_id=slab_id) and payload.get("is_active", bool(row.is_active)):
            raise HTTPException(status_code=409, detail="Slab overlaps an existing active slab")
        row.min_amount = min_amount
        row.max_amount = max_amount
        if "fee_rs" in payload and payload["fee_rs"] is not None:
            try:
                fee = int(payload["fee_rs"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="fee_rs must be a number")
            if fee < 0:
                raise HTTPException(status_code=400, detail="fee_rs must be >= 0")
            row.fee_rs = fee
        if "is_active" in payload and payload["is_active"] is not None:
            row.is_active = 1 if payload["is_active"] else 0
        if "sort_order" in payload and payload["sort_order"] is not None:
            try:
                row.sort_order = int(payload["sort_order"])
            except (TypeError, ValueError):
                pass
        db.commit()
        db.refresh(row)
        return {"status": "success", "slab": _to_dict(row)}

    @staticmethod
    def delete(db: Session, slab_id: int) -> dict:
        row = db.query(PlatformFeeSlab).filter(PlatformFeeSlab.id == slab_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Slab not found")
        db.delete(row)
        db.commit()
        return {"status": "success", "message": "Slab deleted"}
