"""Discount validation and application (Plan 2B #7)."""
import json
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import Discount, DiscountRedemption


def _parse_user_ids(raw) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [int(x) for x in parsed if str(x).strip() != ""]
    except Exception:
        pass
    return []


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class DiscountService:
    @staticmethod
    def validate(code: str, user_id: int, amount: int, db: Session) -> dict:
        if not code or not str(code).strip():
            raise HTTPException(status_code=400, detail="Discount code is required")
        d = db.query(Discount).filter(Discount.code == str(code).strip().upper()).first()
        if not d or not d.is_active:
            raise HTTPException(status_code=404, detail="Invalid discount code")
        today = _today_str()
        if d.valid_from and today < str(d.valid_from):
            raise HTTPException(status_code=400, detail="Discount not yet valid")
        if d.valid_to and today > str(d.valid_to):
            raise HTTPException(status_code=400, detail="Discount expired")
        if d.max_uses is not None and (d.used_count or 0) >= d.max_uses:
            raise HTTPException(status_code=400, detail="Discount usage limit reached")
        if (d.scope or "all") == "users":
            allowed = _parse_user_ids(d.user_ids)
            if user_id not in allowed:
                raise HTTPException(status_code=403, detail="Discount not available for this account")
        try:
            amt = int(amount or 0)
        except (TypeError, ValueError):
            amt = 0
        if amt < int(d.min_amount or 0):
            raise HTTPException(status_code=400, detail=f"Minimum booking amount is Rs{d.min_amount}")
        if (d.discount_type or "flat") == "percent":
            pct = max(0, min(100, int(d.value or 0)))
            off = (amt * pct) // 100
        else:
            off = min(amt, max(0, int(d.value or 0)))
        return {
            "valid": True,
            "code": d.code,
            "discount_type": d.discount_type,
            "value": d.value,
            "amount_off": off,
            "final_amount": max(0, amt - off),
        }

    @staticmethod
    def apply_to_booking(discount_id: int, user_id: int, booking_id: int, amount_off: int, db: Session) -> None:
        d = db.query(Discount).filter(Discount.id == discount_id).first()
        if d:
            d.used_count = (d.used_count or 0) + 1
        db.add(DiscountRedemption(
            discount_id=discount_id,
            user_id=user_id,
            booking_id=booking_id,
            amount_off=amount_off,
        ))
