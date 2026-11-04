from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin, get_current_user
from app.db.database import get_db
from app.schemas.marketing import SupportTicketCreate, SupportTicketReply
from app.services.support_service import SupportService

router = APIRouter()


@router.post("/tickets")
def create_ticket(payload: SupportTicketCreate, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Customer raises a support ticket (24 hrs response promise)."""
    return SupportService.create_ticket(current_user.id, payload.dict(), db)


@router.get("/my")
def my_tickets(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    """Customer lists own tickets (newest first)."""
    return SupportService.my_tickets(current_user.id, db)


@router.get("/tickets/{ticket_id}")
def get_ticket(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return SupportService.get_ticket(current_user.id, ticket_id, db)


@router.get("/admin/tickets")
def admin_tickets(
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    admin: dict = Depends(get_current_admin),
):
    """Admin support queue with pending/completed counts."""
    return SupportService.admin_list(status, category, limit, offset, db)


@router.put("/admin/tickets/{ticket_id}")
def admin_update_ticket(ticket_id: int, payload: SupportTicketReply, db: Session = Depends(get_db), admin: dict = Depends(get_current_admin)):
    return SupportService.admin_update(ticket_id, payload.dict(exclude_unset=True), db)
