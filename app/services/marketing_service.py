"""Marketing suite services: booking config, offers, popups.

Offers feed homepage banners; popups feed the 5s modal; booking_config
controls Kazilen auto-confirm. All admin writes; reads are public where noted.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import BookingConfig, Offer, Popup


def _offer_to_dict(o: Offer) -> dict:
    return {
        "id": o.id,
        "title": o.title,
        "subtitle": o.subtitle,
        "image": o.image,
        "link_type": o.link_type or "none",
        "link_target": o.link_target,
        "discount_code": o.discount_code,
        "is_active": bool(o.is_active),
        "sort_order": o.sort_order or 0,
    }


def _popup_to_dict(p: Popup) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "image": p.image,
        "cta_text": p.cta_text,
        "cta_link": p.cta_link,
        "discount_code": p.discount_code,
        "delay_seconds": p.delay_seconds or 5,
        "is_active": bool(p.is_active),
    }


class MarketingService:
    # -- Booking config (singleton id=1) --
    @staticmethod
    def get_booking_config(db: Session) -> dict:
        cfg = db.query(BookingConfig).filter(BookingConfig.id == 1).first()
        if not cfg:
            cfg = BookingConfig(id=1, auto_confirm=1)
            db.add(cfg)
            db.commit()
            db.refresh(cfg)
        return {"status": "success", "auto_confirm": bool(cfg.auto_confirm)}

    @staticmethod
    def set_booking_config(auto_confirm: bool, db: Session) -> dict:
        cfg = db.query(BookingConfig).filter(BookingConfig.id == 1).first()
        if not cfg:
            cfg = BookingConfig(id=1, auto_confirm=1 if auto_confirm else 0)
            db.add(cfg)
        else:
            cfg.auto_confirm = 1 if auto_confirm else 0
        db.commit()
        db.refresh(cfg)
        return {"status": "success", "auto_confirm": bool(cfg.auto_confirm)}

    # -- Offers --
    @staticmethod
    def public_offers(db: Session) -> dict:
        rows = (
            db.query(Offer)
            .filter(Offer.is_active == 1)
            .order_by(Offer.sort_order.asc(), Offer.id.desc())
            .all()
        )
        return {"status": "success", "offers": [_offer_to_dict(r) for r in rows]}

    @staticmethod
    def admin_offers(db: Session) -> dict:
        rows = db.query(Offer).order_by(Offer.sort_order.asc(), Offer.id.desc()).all()
        return {"status": "success", "offers": [_offer_to_dict(r) for r in rows]}

    @staticmethod
    def create_offer(payload: dict, db: Session) -> dict:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        link_type = str(payload.get("link_type") or "none").strip().lower()
        if link_type not in ("none", "category", "discount", "referral", "custom"):
            raise HTTPException(status_code=400, detail="Invalid link_type")
        o = Offer(
            title=title,
            subtitle=(str(payload.get("subtitle") or "").strip() or None),
            image=(str(payload.get("image") or "").strip() or None),
            link_type=link_type,
            link_target=(str(payload.get("link_target") or "").strip() or None),
            discount_code=(str(payload.get("discount_code") or "").strip().upper() or None),
            is_active=1 if payload.get("is_active", True) else 0,
            sort_order=int(payload.get("sort_order") or 0),
        )
        db.add(o)
        db.commit()
        db.refresh(o)
        return {"status": "success", "offer": _offer_to_dict(o)}

    @staticmethod
    def update_offer(offer_id: int, payload: dict, db: Session) -> dict:
        o = db.query(Offer).filter(Offer.id == offer_id).first()
        if not o:
            raise HTTPException(status_code=404, detail="Offer not found")
        if "title" in payload and payload["title"] is not None:
            t = str(payload["title"]).strip()
            if not t:
                raise HTTPException(status_code=400, detail="Title cannot be empty")
            o.title = t
        for key in ("subtitle", "image", "link_target", "cta_text", "cta_link"):
            if key in payload:
                v = payload[key]
                setattr(o, key, (str(v).strip() or None) if v is not None else None)
        if "link_type" in payload and payload["link_type"] is not None:
            lt = str(payload["link_type"]).strip().lower()
            if lt not in ("none", "category", "discount", "referral", "custom"):
                raise HTTPException(status_code=400, detail="Invalid link_type")
            o.link_type = lt
        if "discount_code" in payload:
            v = payload["discount_code"]
            o.discount_code = (str(v).strip().upper() or None) if v is not None else None
        if "is_active" in payload and payload["is_active"] is not None:
            o.is_active = 1 if payload["is_active"] else 0
        if "sort_order" in payload and payload["sort_order"] is not None:
            try:
                o.sort_order = int(payload["sort_order"])
            except (TypeError, ValueError):
                pass
        db.commit()
        db.refresh(o)
        return {"status": "success", "offer": _offer_to_dict(o)}

    @staticmethod
    def delete_offer(offer_id: int, db: Session) -> dict:
        o = db.query(Offer).filter(Offer.id == offer_id).first()
        if not o:
            raise HTTPException(status_code=404, detail="Offer not found")
        db.delete(o)
        db.commit()
        return {"status": "success", "message": "Offer deleted"}

    # -- Popups --
    @staticmethod
    def active_popup(db: Session) -> dict:
        p = (
            db.query(Popup)
            .filter(Popup.is_active == 1)
            .order_by(Popup.id.desc())
            .first()
        )
        return {"status": "success", "popup": _popup_to_dict(p) if p else None}

    @staticmethod
    def admin_popups(db: Session) -> dict:
        rows = db.query(Popup).order_by(Popup.id.desc()).all()
        return {"status": "success", "popups": [_popup_to_dict(r) for r in rows]}

    @staticmethod
    def create_popup(payload: dict, db: Session) -> dict:
        title = str(payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        try:
            delay = int(payload.get("delay_seconds") or 5)
        except (TypeError, ValueError):
            delay = 5
        delay = max(1, min(60, delay))
        p = Popup(
            title=title,
            description=(str(payload.get("description") or "").strip() or None),
            image=(str(payload.get("image") or "").strip() or None),
            cta_text=(str(payload.get("cta_text") or "").strip() or None),
            cta_link=(str(payload.get("cta_link") or "").strip() or None),
            discount_code=(str(payload.get("discount_code") or "").strip().upper() or None),
            delay_seconds=delay,
            is_active=1 if payload.get("is_active", False) else 0,
        )
        db.add(p)
        # Only one active popup at a time — activating here deactivates others.
        if p.is_active:
            for other in db.query(Popup).filter(Popup.id != p.id, Popup.is_active == 1).all():
                other.is_active = 0
        db.commit()
        db.refresh(p)
        return {"status": "success", "popup": _popup_to_dict(p)}

    @staticmethod
    def update_popup(popup_id: int, payload: dict, db: Session) -> dict:
        p = db.query(Popup).filter(Popup.id == popup_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Popup not found")
        if "title" in payload and payload["title"] is not None:
            t = str(payload["title"]).strip()
            if not t:
                raise HTTPException(status_code=400, detail="Title cannot be empty")
            p.title = t
        for key in ("description", "image", "cta_text", "cta_link"):
            if key in payload:
                v = payload[key]
                setattr(p, key, (str(v).strip() or None) if v is not None else None)
        if "discount_code" in payload:
            v = payload["discount_code"]
            p.discount_code = (str(v).strip().upper() or None) if v is not None else None
        if "delay_seconds" in payload and payload["delay_seconds"] is not None:
            try:
                p.delay_seconds = max(1, min(60, int(payload["delay_seconds"])))
            except (TypeError, ValueError):
                pass
        if "is_active" in payload and payload["is_active"] is not None:
            active = bool(payload["is_active"])
            p.is_active = 1 if active else 0
            if active:
                for other in db.query(Popup).filter(Popup.id != p.id, Popup.is_active == 1).all():
                    other.is_active = 0
        db.commit()
        db.refresh(p)
        return {"status": "success", "popup": _popup_to_dict(p)}

    @staticmethod
    def delete_popup(popup_id: int, db: Session) -> dict:
        p = db.query(Popup).filter(Popup.id == popup_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Popup not found")
        db.delete(p)
        db.commit()
        return {"status": "success", "message": "Popup deleted"}
