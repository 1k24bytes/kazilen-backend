import re
import secrets
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_admin_token, verify_password
from app.db.models import Booking, BookingReview, User
from app.schemas.admin import AdminLoginRequest
from app.services.referral_service import generate_unique_referral_code


class AdminService:
    @staticmethod
    def _verify_admin_password(plain_password: str) -> bool:
        """Verify the admin password.

        PRIMARY path: bcrypt comparison against ``ADMIN_PASSWORD_HASH``.
        FALLBACK path (dev convenience): constant-time comparison against
        the plain ``ADMIN_PASSWORD`` env value.
        """
        if settings.ADMIN_PASSWORD_HASH:
            return verify_password(plain_password, settings.ADMIN_PASSWORD_HASH)
        if settings.ADMIN_PASSWORD:
            return secrets.compare_digest(plain_password, settings.ADMIN_PASSWORD)
        return False

    @staticmethod
    def login(request: AdminLoginRequest) -> dict:
        """Authenticate the single env-configured admin and issue a JWT."""
        if not settings.ADMIN_EMAIL or not request.email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        email_ok = secrets.compare_digest(
            request.email.strip().lower(), settings.ADMIN_EMAIL.strip().lower()
        )
        password_ok = AdminService._verify_admin_password(request.password)

        if not (email_ok and password_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        access_token = create_admin_token(email=settings.ADMIN_EMAIL)
        return {
            "status": "success",
            "access_token": access_token,
            "token_type": "bearer",
            "email": settings.ADMIN_EMAIL,
        }

    @staticmethod
    def get_stats(db: Session) -> dict:
        """Aggregate platform-wide counts for the admin dashboard."""
        return {
            "total_users": db.query(User).count(),
            "total_customers": db.query(User).filter(User.role == "customer").count(),
            "total_workers": db.query(User).filter(User.role == "worker").count(),
            "workers_online": db.query(User)
            .filter(User.role == "worker", User.is_online == 1)
            .count(),
            "total_bookings": db.query(Booking).count(),
            "bookings_pending": db.query(Booking)
            .filter(Booking.status == "pending")
            .count(),
            "bookings_completed": db.query(Booking)
            .filter(Booking.status == "completed")
            .count(),
            "total_reviews": db.query(BookingReview).count(),
        }

    @staticmethod
    def normalize_phone(phone_number: str) -> str:
        """Normalizes to the canonical `<country-code><10-digit>` format.

        Mirrors the customer/worker frontends: a plain 10-digit number gets
        the `91` prefix; anything else is kept digits-only as entered.
        """
        digits = re.sub(r"\D", "", phone_number or "")
        if len(digits) == 10:
            digits = f"91{digits}"
        return digits

    @staticmethod
    def create_worker(
        db: Session,
        phone_number: str,
        full_name: str,
        dob: str | None = None,
        gender: str | None = None,
        is_online: bool = True,
    ) -> User:
        """Creates a new worker account directly (admin bypasses OTP)."""
        phone = AdminService.normalize_phone(phone_number)
        if not re.fullmatch(r"\d{12}", phone):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Enter a valid 10-digit mobile number",
            )

        name = (full_name or "").strip()
        if not name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Full name is required",
            )

        existing = (
            db.query(User)
            .filter(User.phone_number == phone, User.role == "worker")
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A worker with this phone number already exists",
            )

        worker = User(
            phone_number=phone,
            full_name=name,
            role="worker",
            dob=(dob or None),
            gender=(gender or None),
            referral_code=generate_unique_referral_code(db),
            referral_points=0,
            is_online=1 if is_online else 0,
        )
        db.add(worker)
        db.commit()
        db.refresh(worker)
        return worker
