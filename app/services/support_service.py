"""Customer support tickets (feature/marketing #7)."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import SupportTicket, User

ALLOWED_CATEGORIES = ("bookings", "refund", "query", "payment", "other")
ALLOWED_STATUS = ("pending", "in_progress", "completed")


def _to_dict(t: SupportTicket, db: Session) -> dict:
    user = db.query(User).filter(User.id == t.user_id).first()
    return {
        "id": t.id,
        "user_id": t.user_id,
        "user_name": user.full_name if user else None,
        "phone_number": user.phone_number if user else None,
        "role": user.role if user else None,
        "category": t.category,
        "subject": t.subject,
        "description": t.description,
        "booking_id": t.booking_id,
        "status": t.status,
        "admin_reply": t.admin_reply,
        "created_at": str(t.created_at) if t.created_at else None,
        "updated_at": str(t.updated_at) if t.updated_at else None,
    }


class SupportService:
    @staticmethod
    def create_ticket(user_id: int, payload: dict, db: Session) -> dict:
        category = str(payload.get("category") or "query").strip().lower()
        if category not in ALLOWED_CATEGORIES:
            raise HTTPException(status_code=400, detail="Invalid category")
        description = str(payload.get("description") or "").strip()
        if len(description) < 6:
            raise HTTPException(status_code=400, detail="Please describe the issue (min 6 characters)")
        booking_id = payload.get("booking_id")
        if booking_id is not None:
            try:
                booking_id = int(booking_id)
            except (TypeError, ValueError):
                booking_id = None
        t = SupportTicket(
            user_id=user_id,
            category=category,
            subject=(str(payload.get("subject") or "").strip() or None),
            description=description,
            booking_id=booking_id,
            status="pending",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return {"status": "success", "message": "Ticket raised. We will reach you in 24 hrs.", "ticket": _to_dict(t, db)}

    @staticmethod
    def my_tickets(user_id: int, db: Session) -> dict:
        rows = db.query(SupportTicket).filter(SupportTicket.user_id == user_id).order_by(SupportTicket.id.desc()).all()
        return {"status": "success", "tickets": [_to_dict(r, db) for r in rows]}

    @staticmethod
    def get_ticket(user_id: int, ticket_id: int, db: Session, admin: bool = False) -> dict:
        t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if not admin and t.user_id != user_id:
            raise HTTPException(status_code=403, detail="Access denied")
        return {"status": "success", "ticket": _to_dict(t, db)}

    @staticmethod
    def admin_list(status_filter: str | None, category: str | None, limit: int, offset: int, db: Session) -> dict:
        q = db.query(SupportTicket).order_by(SupportTicket.id.desc())
        if status_filter:
            q = q.filter(SupportTicket.status == status_filter)
        if category:
            q = q.filter(SupportTicket.category == category)
        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        pending = db.query(SupportTicket).filter(SupportTicket.status == "pending").count()
        completed = db.query(SupportTicket).filter(SupportTicket.status == "completed").count()
        return {
            "status": "success",
            "total": total,
            "pending_count": pending,
            "completed_count": completed,
            "tickets": [_to_dict(r, db) for r in rows],
        }

    @staticmethod
    def admin_update(ticket_id: int, payload: dict, db: Session) -> dict:
        t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Ticket not found")
        if "status" in payload and payload["status"] is not None:
            st = str(payload["status"]).strip().lower()
            if st not in ALLOWED_STATUS:
                raise HTTPException(status_code=400, detail="Invalid status")
            t.status = st
        if "admin_reply" in payload:
            v = payload["admin_reply"]
            t.admin_reply = (str(v).strip() or None) if v is not None else None
        db.commit()
        db.refresh(t)
        return {"status": "success", "ticket": _to_dict(t, db)}
