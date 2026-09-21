import random
import re
import redis
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.db.models import ReferralClaim, User
from app.core.config import settings
from app.core.security import hash_otp, create_access_token
from app.schemas.auth import SendOTPRequest, VerifyOTPRequest, RegisterRequest
from app.services.referral_service import generate_unique_referral_code
from app.services.otp_service import OTPService

redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
memory_otp_store = {}

class AuthService:
    @staticmethod
    def send_otp(request: SendOTPRequest) -> dict:
        otp = str(random.randint(100000, 999999))
        
        # Always print OTP to dev terminal during development
        print(f"\n=======================================================", flush=True)
        print(f"--- DEV OTP FOR {request.phone_number}: {otp} ---", flush=True)
        print(f"=======================================================\n", flush=True)

        # Dispatch OTP via vendor-agnostic OTPService (Meta WhatsApp, Twilio, Console, or Custom)
        OTPService.send_otp(request.phone_number, otp)
        
        hashed_otp = hash_otp(otp)
        redis_key = f"otp:{request.phone_number}"
        
        try:
            redis_client.setex(redis_key, 300, hashed_otp)  # 5 minutes expiry
        except Exception as e:
            print(f"[WARN] Redis unavailable ({e}), using in-memory OTP fallback.", flush=True)
            memory_otp_store[request.phone_number] = hashed_otp
            
        return {"message": "OTP sent successfully", "dev_otp": otp}



    @staticmethod
    def verify_otp(request: VerifyOTPRequest, db: Session) -> dict:
        redis_key = f"otp:{request.phone_number}"
        stored_hashed_otp = None
        
        try:
            stored_hashed_otp = redis_client.get(redis_key)
        except Exception:
            stored_hashed_otp = memory_otp_store.get(request.phone_number)

        # Allow 123456 as fallback master dev OTP
        is_master_dev_otp = (request.otp == "123456")
        
        if not stored_hashed_otp and not is_master_dev_otp:
            stored_hashed_otp = memory_otp_store.get(request.phone_number)

        if not stored_hashed_otp and not is_master_dev_otp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OTP expired or not requested. Try sending OTP again."
            )
            
        if not is_master_dev_otp and hash_otp(request.otp) != stored_hashed_otp:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid OTP entered"
            )
            
        # Clean up OTP
        try:
            redis_client.delete(redis_key)
        except Exception:
            memory_otp_store.pop(request.phone_number, None)
        
        # Check if user exists
        user = db.query(User).filter(
            User.phone_number == request.phone_number,
            User.role == request.role
        ).first()
        
        if not user:
            return {
                "status": "needs_registration",
                "phone_number": request.phone_number,
                "role": request.role
            }
            
        # User exists, issue JWT
        access_token = create_access_token(subject=user.id)
        return {
            "status": "success",
            "access_token": access_token,
            "token_type": "bearer"
        }

    @staticmethod
    def register(request: RegisterRequest, db: Session) -> dict:
        existing_user = db.query(User).filter(
            User.phone_number == request.phone_number,
            User.role == request.role
        ).first()
        
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User already registered"
            )
            
        referral_code = (request.referral_code or "").strip().upper()
        referrer = None
        if referral_code:
            if not re.fullmatch(r"[A-Z0-9]{6}", referral_code):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Referral code must be 6 letters or numbers",
                )

            referrer = db.query(User).filter(
                User.referral_code == referral_code,
            ).first()
            if not referrer:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Referral code is invalid or expired",
                )

        # New workers start "pending" (in review by admin) and stay hidden
        # from the marketplace until approved. Customers are always "approved".
        verification = "pending" if request.role == "worker" else "approved"
        new_user = User(
            phone_number=request.phone_number,
            full_name=request.full_name,
            role=request.role,
            dob=request.dob,
            gender=request.gender,
            referral_code=generate_unique_referral_code(db),
            referral_points=0,
            is_online=1,
            verification_status=verification,
        )
        db.add(new_user)
        db.flush()

        if referrer:
            referrer.referral_points = (referrer.referral_points or 0) + 1
            db.add(ReferralClaim(
                referrer_id=referrer.id,
                referred_user_id=new_user.id,
                referral_code=referral_code,
            ))

        db.commit()
        db.refresh(new_user)

        access_token = create_access_token(subject=new_user.id)
        return {
            "status": "success",
            "access_token": access_token,
            "token_type": "bearer",
            "verification_status": new_user.verification_status or "approved",
        }
