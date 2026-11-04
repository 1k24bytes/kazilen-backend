"""Razorpay payments for worker membership subscriptions.

Port of live_wahhtrips payment flow (controllers/paymentController.js):
- create order server-side (amount from DB plan.price, never client)
- client verify: HMAC_SHA256(key_secret, order_id|payment_id) + payments.fetch == captured
- webhook: HMAC_SHA256(webhook_secret, raw_body) == x-razorpay-signature,
  event payment.captured activates (fallback when client verify is skipped)
- idempotency on razorpay_payment_id (like wahhtrips unique Transaction.transactionId)
  + early return when already captured.

Manual admin subscriptions (membership_service.subscribe) are untouched and
bypass this module entirely.
"""

import hashlib
import hmac
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import (
    Booking,
    BookingPayment,
    MembershipPlan,
    MembershipSubscription,
    SubscriptionPayment,
    User,
)
from app.services.membership_service import MembershipService


def _require_keys():
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Online payments are not configured. Contact admin or ask for manual activation.",
        )


def _get_client():
    """Lazy razorpay SDK import so the app boots without the package installed."""
    _require_keys()
    try:
        import razorpay
    except ImportError:
        raise HTTPException(
            status_code=503,
            detail="Payment gateway library missing on server (pip install razorpay).",
        )
    return razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))


def _payment_to_dict(p: SubscriptionPayment, db: Session) -> dict:
    worker = db.query(User).filter(User.id == p.worker_id).first()
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == p.plan_id).first()
    created = p.created_at.isoformat() if getattr(p, "created_at", None) else None
    return {
        "id": p.id,
        "worker_id": p.worker_id,
        "worker_name": worker.full_name if worker else None,
        "plan_id": p.plan_id,
        "plan_name": plan.name if plan else None,
        "subscription_id": p.subscription_id,
        "amount": p.amount or 0,
        "currency": p.currency or "INR",
        "razorpay_order_id": p.razorpay_order_id,
        "razorpay_payment_id": p.razorpay_payment_id,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "created_at": created,
    }


def _booking_payment_to_dict(p: BookingPayment, db: Session) -> dict:
    customer = db.query(User).filter(User.id == p.customer_id).first()
    worker = db.query(User).filter(User.id == p.worker_id).first()
    created = p.created_at.isoformat() if getattr(p, "created_at", None) else None
    return {
        "id": p.id,
        "booking_id": p.booking_id,
        "customer_id": p.customer_id,
        "customer_name": customer.full_name if customer else None,
        "worker_id": p.worker_id,
        "worker_name": worker.full_name if worker else None,
        "base_amount": p.base_amount or 0,
        "platform_fee": p.platform_fee or 0,
        "total_amount": p.total_amount or 0,
        "currency": p.currency or "INR",
        "razorpay_order_id": p.razorpay_order_id,
        "razorpay_payment_id": p.razorpay_payment_id,
        "status": p.status,
        "failure_reason": p.failure_reason,
        "created_at": created,
    }


def _activate_booking_payment(db: Session, payment: BookingPayment) -> None:
    """Mark booking as paid_online for a captured booking payment (idempotent)."""
    if payment.status == "captured":
        booking = db.query(Booking).filter(Booking.id == payment.booking_id).first()
        if booking and getattr(booking, "payment_status", "") != "paid_online":
            booking.payment_status = "paid_online"
            db.commit()
        return
    booking = db.query(Booking).filter(Booking.id == payment.booking_id).first()
    if booking and getattr(booking, "payment_status", "") == "paid_online":
        return
    payment.status = "captured"
    db.commit()
    db.refresh(payment)
    if booking:
        booking.payment_status = "paid_online"
        booking.total_amount = str(payment.total_amount or booking.total_amount or booking.amount or "0")
        booking.platform_fee = str(payment.platform_fee or booking.platform_fee or "0")
        db.commit()


def _activate_paid_subscription(db: Session, payment: SubscriptionPayment) -> dict:
    """Create/refresh the active subscription window for a captured payment.

    Same semantics as MembershipService.self_subscribe (cancel prior actives,
    fresh window from now) but driven by a verified payment, not a demo click.
    Idempotent: if payment.subscription_id already points at a live row, reuse it.
    """
    if payment.subscription_id:
        sub = (
            db.query(MembershipSubscription)
            .filter(MembershipSubscription.id == payment.subscription_id)
            .first()
        )
        if sub and sub.status == "active":
            return MembershipService.worker_quota(payment.worker_id, db)

    worker = (
        db.query(User)
        .filter(User.id == payment.worker_id, User.role == "worker")
        .first()
    )
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")
    plan = db.query(MembershipPlan).filter(MembershipPlan.id == payment.plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Membership plan not found")

    now = datetime.now(timezone.utc)
    old = (
        db.query(MembershipSubscription)
        .filter(
            MembershipSubscription.worker_id == payment.worker_id,
            MembershipSubscription.status == "active",
        )
        .all()
    )
    for sub in old:
        sub.status = "cancelled"
    expires = now + __import__("datetime").timedelta(days=plan.duration_days or 30)
    sub = MembershipSubscription(
        worker_id=payment.worker_id,
        plan_id=payment.plan_id,
        status="active",
        starts_at=now,
        expires_at=expires,
    )
    db.add(sub)
    db.flush()  # get sub.id before commit
    payment.subscription_id = sub.id
    payment.status = "captured"
    db.commit()
    db.refresh(sub)
    db.refresh(payment)
    return MembershipService.worker_quota(payment.worker_id, db)


class PaymentService:
    @staticmethod
    def create_subscription_order(worker_id: int, plan_id: int, db: Session) -> dict:
        worker = (
            db.query(User)
            .filter(User.id == worker_id, User.role == "worker")
            .first()
        )
        if not worker:
            raise HTTPException(status_code=404, detail="Worker not found")
        plan = (
            db.query(MembershipPlan)
            .filter(MembershipPlan.id == plan_id, MembershipPlan.is_active == 1)
            .first()
        )
        if not plan:
            raise HTTPException(status_code=404, detail="Membership plan not found or inactive")

        amount_rs = int(plan.price or 0)
        if amount_rs <= 0:
            raise HTTPException(
                status_code=400,
                detail="This plan is free — use Subscribe instead of online payment.",
            )

        client = _get_client()
        # Create our audit row first so receipt is stable even if SDK call fails.
        payment = SubscriptionPayment(
            worker_id=worker_id,
            plan_id=plan_id,
            amount=amount_rs,
            currency="INR",
            razorpay_order_id="pending",
            status="created",
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

        try:
            order = client.order.create(
                {
                    "amount": amount_rs * 100,  # paise
                    "currency": "INR",
                    "receipt": f"kazilen_sub_{payment.id}",
                    "notes": {
                        "worker_id": str(worker_id),
                        "plan_id": str(plan_id),
                        "payment_id": str(payment.id),
                        "type": "membership_subscription",
                    },
                    "payment_capture": 1,
                }
            )
        except Exception as exc:
            payment.status = "failed"
            payment.failure_reason = f"order_create_failed:{str(exc)[:200]}"
            db.commit()
            raise HTTPException(status_code=502, detail="Failed to create payment order. Try again.")

        payment.razorpay_order_id = order["id"]
        db.commit()
        db.refresh(payment)
        return {
            "status": "success",
            "order_id": order["id"],
            "amount": amount_rs * 100,
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID,
            "plan_id": plan.id,
            "plan_name": plan.name,
            "payment_id": payment.id,
        }

    @staticmethod
    def order_status(worker_id: int, order_id: str, db: Session) -> dict:
        payment = (
            db.query(SubscriptionPayment)
            .filter(
                SubscriptionPayment.razorpay_order_id == order_id,
                SubscriptionPayment.worker_id == worker_id,
            )
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Payment order not found")
        return {"status": "success", "payment": _payment_to_dict(payment, db)}

    @staticmethod
    def verify_subscription_payment(
        worker_id: int, order_id: str, razorpay_payment_id: str, signature: str, db: Session
    ) -> dict:
        _require_keys()
        # HMAC check first (wahhtrips verifyClientPayment L431).
        expected = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            f"{order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        payment = (
            db.query(SubscriptionPayment)
            .filter(
                SubscriptionPayment.razorpay_order_id == order_id,
                SubscriptionPayment.worker_id == worker_id,
            )
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Payment order not found")

        # Idempotency: same payment seen twice (client + webhook race).
        if payment.status == "captured" and payment.razorpay_payment_id == razorpay_payment_id:
            quota = MembershipService.worker_quota(worker_id, db)
            return {
                "status": "success",
                "message": "Payment already processed.",
                "payment": _payment_to_dict(payment, db),
                "quota": quota,
            }
        dup = (
            db.query(SubscriptionPayment)
            .filter(SubscriptionPayment.razorpay_payment_id == razorpay_payment_id)
            .first()
        )
        if dup and dup.id != payment.id and dup.status == "captured":
            raise HTTPException(status_code=409, detail="Payment already used for another order")

        # Server-side capture check (never trust the client modal alone).
        client = _get_client()
        try:
            fetched = client.payment.fetch(razorpay_payment_id)
        except Exception as exc:
            payment.status = "failed"
            payment.failure_reason = f"fetch_failed:{str(exc)[:200]}"
            db.commit()
            raise HTTPException(status_code=502, detail="Could not confirm payment with gateway.")
        if fetched.get("status") != "captured":
            payment.status = "failed"
            payment.failure_reason = f"payment_not_captured:{fetched.get('status')}"
            payment.raw_payload = json.dumps({"status": fetched.get("status")})[:4000]
            db.commit()
            raise HTTPException(status_code=400, detail="Payment not captured")
        # Amount guard: paid paise must match plan price paise.
        paid_amount = fetched.get("amount")
        if paid_amount is not None and int(paid_amount) != int(payment.amount or 0) * 100:
            payment.status = "failed"
            payment.failure_reason = f"amount_mismatch:expected={int(payment.amount or 0) * 100},got={paid_amount}"
            db.commit()
            raise HTTPException(status_code=400, detail="Paid amount does not match plan price")

        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = signature
        payment.raw_payload = json.dumps(
            {"method": fetched.get("method"), "status": fetched.get("status")}
        )[:4000]
        db.commit()
        db.refresh(payment)
        quota = _activate_paid_subscription(db, payment)
        db.refresh(payment)
        return {
            "status": "success",
            "message": "Payment verified. Subscription activated.",
            "payment": _payment_to_dict(payment, db),
            "quota": quota,
        }

    @staticmethod
    def handle_webhook(raw_body: bytes, signature: str | None, payload: dict, db: Session) -> dict:
        """Razorpay server webhook. Returns dict; raises HTTPException on bad signature."""
        if not settings.RAZORPAY_WEBHOOK_SECRET:
            raise HTTPException(status_code=503, detail="Webhook secret not configured")
        if not signature:
            raise HTTPException(status_code=400, detail="Missing webhook signature")
        digest = hmac.new(settings.RAZORPAY_WEBHOOK_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            raise HTTPException(status_code=400, detail="Invalid webhook signature")

        event = payload.get("event", "")
        entity = (
            payload.get("payload", {}).get("payment", {}).get("entity")
            or payload.get("payload", {}).get("entity")
            or {}
        )
        if event != "payment.captured":
            return {"status": "success", "message": f"Ignored event {event}"}
        order_id = entity.get("order_id")
        gateway_payment_id = entity.get("id")
        if not order_id or not gateway_payment_id:
            raise HTTPException(status_code=400, detail="Invalid webhook payload")

        payment = (
            db.query(SubscriptionPayment)
            .filter(SubscriptionPayment.razorpay_order_id == order_id)
            .first()
        )
        if payment:
            if payment.status == "captured":
                return {"status": "success", "message": "Webhook already processed."}
            # Another payment id already captured for this order — keep first winner.
            if payment.razorpay_payment_id and payment.razorpay_payment_id != gateway_payment_id:
                return {"status": "success", "message": "Order already paid with another payment; ignored"}

            payment.razorpay_payment_id = gateway_payment_id
            payment.raw_payload = json.dumps(
                {"method": entity.get("method"), "amount": entity.get("amount"), "event": event}
            )[:4000]
            payment.status = "webhook_received"
            db.commit()
            db.refresh(payment)
            _activate_paid_subscription(db, payment)
            return {"status": "success", "message": "Subscription activated via webhook."}

        # Fall through to booking payments (customer online checkout).
        bpay = (
            db.query(BookingPayment)
            .filter(BookingPayment.razorpay_order_id == order_id)
            .first()
        )
        if not bpay:
            # Unknown order — ack to stop retries.
            return {"status": "success", "message": "Order not tracked; ignored"}
        if bpay.status == "captured":
            return {"status": "success", "message": "Webhook already processed."}
        if bpay.razorpay_payment_id and bpay.razorpay_payment_id != gateway_payment_id:
            return {"status": "success", "message": "Order already paid with another payment; ignored"}
        bpay.razorpay_payment_id = gateway_payment_id
        bpay.raw_payload = json.dumps(
            {"method": entity.get("method"), "amount": entity.get("amount"), "event": event}
        )[:4000]
        bpay.status = "webhook_received"
        db.commit()
        db.refresh(bpay)
        _activate_booking_payment(db, bpay)
        return {"status": "success", "message": "Booking confirmed via webhook."}

    @staticmethod
    def list_payments(db: Session, worker_id: int | None = None, status_filter: str | None = None,
                      limit: int = 50, offset: int = 0) -> dict:
        query = db.query(SubscriptionPayment).order_by(SubscriptionPayment.id.desc())
        if worker_id:
            query = query.filter(SubscriptionPayment.worker_id == worker_id)
        if status_filter:
            query = query.filter(SubscriptionPayment.status == status_filter)
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
        return {
            "status": "success",
            "total": total,
            "payments": [_payment_to_dict(r, db) for r in rows],
        }

    # ------------------------------------------------------------------
    # Customer booking online payments (feature/payment)
    # ------------------------------------------------------------------

    @staticmethod
    def create_booking_order(customer_id: int, booking_id: int, db: Session) -> dict:
        from app.services.platform_fee_service import PlatformFeeService

        booking = db.query(Booking).filter(Booking.id == booking_id).first()
        if not booking:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.customer_id != customer_id:
            raise HTTPException(status_code=403, detail="Access denied")
        if getattr(booking, "payment_mode", "cash") != "online":
            raise HTTPException(status_code=400, detail="Booking is not an online payment booking")
        if getattr(booking, "payment_status", "") == "paid_online":
            raise HTTPException(status_code=400, detail="Booking already paid online")

        try:
            base = int(float(str(booking.amount or "0").strip() or 0))
        except (TypeError, ValueError):
            base = 0
        quote = PlatformFeeService.quote(db, base)
        fee = int(quote["platform_fee"] or 0)
        total = base + fee

        # Idempotency: reuse pending order for same booking if still created.
        existing = (
            db.query(BookingPayment)
            .filter(BookingPayment.booking_id == booking.id)
            .order_by(BookingPayment.id.desc())
            .first()
        )
        if existing and existing.status == "created" and existing.total_amount == total:
            return {
                "status": "success",
                "order_id": existing.razorpay_order_id,
                "amount": total * 100,
                "currency": "INR",
                "key_id": settings.RAZORPAY_KEY_ID,
                "booking_id": booking.id,
                "base_amount": base,
                "platform_fee": fee,
                "total_amount": total,
                "payment_id": existing.id,
            }

        client = _get_client()
        payment = BookingPayment(
            booking_id=booking.id,
            customer_id=customer_id,
            worker_id=booking.worker_id,
            base_amount=base,
            platform_fee=fee,
            total_amount=total,
            currency="INR",
            razorpay_order_id="pending",
            status="created",
        )
        db.add(payment)
        db.commit()
        db.refresh(payment)

        try:
            order = client.order.create(
                {
                    "amount": total * 100,
                    "currency": "INR",
                    "receipt": f"kazilen_booking_{payment.id}",
                    "notes": {
                        "booking_id": str(booking.id),
                        "customer_id": str(customer_id),
                        "worker_id": str(booking.worker_id),
                        "payment_id": str(payment.id),
                        "type": "booking_payment",
                    },
                    "payment_capture": 1,
                }
            )
        except Exception as exc:
            payment.status = "failed"
            payment.failure_reason = f"order_create_failed:{str(exc)[:200]}"
            db.commit()
            raise HTTPException(status_code=502, detail="Failed to create payment order. Try again.")

        payment.razorpay_order_id = order["id"]
        booking.platform_fee = str(fee)
        booking.total_amount = str(total)
        booking.razorpay_order_id = order["id"]
        booking.payment_status = "awaiting_online"
        db.commit()
        db.refresh(payment)
        return {
            "status": "success",
            "order_id": order["id"],
            "amount": total * 100,
            "currency": "INR",
            "key_id": settings.RAZORPAY_KEY_ID,
            "booking_id": booking.id,
            "base_amount": base,
            "platform_fee": fee,
            "total_amount": total,
            "payment_id": payment.id,
        }

    @staticmethod
    def verify_booking_payment(customer_id: int, order_id: str, razorpay_payment_id: str, signature: str, db: Session) -> dict:
        _require_keys()
        expected = hmac.new(
            settings.RAZORPAY_KEY_SECRET.encode(),
            f"{order_id}|{razorpay_payment_id}".encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        payment = (
            db.query(BookingPayment)
            .filter(BookingPayment.razorpay_order_id == order_id)
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Payment order not found")
        if payment.customer_id != customer_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if payment.status == "captured" and payment.razorpay_payment_id == razorpay_payment_id:
            booking = db.query(Booking).filter(Booking.id == payment.booking_id).first()
            return {
                "status": "success",
                "message": "Payment already processed.",
                "payment": _booking_payment_to_dict(payment, db),
                "booking_id": payment.booking_id,
                "payment_status": getattr(booking, "payment_status", "paid_online"),
            }
        dup = (
            db.query(BookingPayment)
            .filter(BookingPayment.razorpay_payment_id == razorpay_payment_id)
            .first()
        )
        if dup and dup.id != payment.id and dup.status == "captured":
            raise HTTPException(status_code=409, detail="Payment already used for another order")

        client = _get_client()
        try:
            fetched = client.payment.fetch(razorpay_payment_id)
        except Exception:
            payment.status = "failed"
            payment.failure_reason = "fetch_failed"
            db.commit()
            raise HTTPException(status_code=502, detail="Could not confirm payment with gateway.")
        if fetched.get("status") != "captured":
            payment.status = "failed"
            payment.failure_reason = f"payment_not_captured:{fetched.get('status')}"
            payment.raw_payload = json.dumps({"status": fetched.get("status")})[:4000]
            db.commit()
            raise HTTPException(status_code=400, detail="Payment not captured")
        paid_amount = fetched.get("amount")
        if paid_amount is not None and int(paid_amount) != int(payment.total_amount or 0) * 100:
            payment.status = "failed"
            payment.failure_reason = f"amount_mismatch:expected={int(payment.total_amount or 0) * 100},got={paid_amount}"
            db.commit()
            raise HTTPException(status_code=400, detail="Paid amount does not match booking total")

        payment.razorpay_payment_id = razorpay_payment_id
        payment.razorpay_signature = signature
        payment.raw_payload = json.dumps(
            {"method": fetched.get("method"), "status": fetched.get("status")}
        )[:4000]
        db.commit()
        db.refresh(payment)
        _activate_booking_payment(db, payment)
        db.refresh(payment)
        return {
            "status": "success",
            "message": "Payment verified. Booking confirmed online.",
            "payment": _booking_payment_to_dict(payment, db),
            "booking_id": payment.booking_id,
            "payment_status": "paid_online",
        }

    @staticmethod
    def booking_order_status(customer_id: int, order_id: str, db: Session) -> dict:
        payment = (
            db.query(BookingPayment)
            .filter(
                BookingPayment.razorpay_order_id == order_id,
                BookingPayment.customer_id == customer_id,
            )
            .first()
        )
        if not payment:
            raise HTTPException(status_code=404, detail="Payment order not found")
        return {"status": "success", "payment": _booking_payment_to_dict(payment, db)}

    @staticmethod
    def list_booking_payments(db: Session, customer_id: int | None = None, worker_id: int | None = None,
                              status_filter: str | None = None, limit: int = 50, offset: int = 0) -> dict:
        query = db.query(BookingPayment).order_by(BookingPayment.id.desc())
        if customer_id:
            query = query.filter(BookingPayment.customer_id == customer_id)
        if worker_id:
            query = query.filter(BookingPayment.worker_id == worker_id)
        if status_filter:
            query = query.filter(BookingPayment.status == status_filter)
        total = query.count()
        rows = query.offset(offset).limit(limit).all()
        return {
            "status": "success",
            "total": total,
            "payments": [_booking_payment_to_dict(r, db) for r in rows],
        }
