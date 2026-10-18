import json
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.db.models import User, Address
from app.schemas.user import (
    UserUpdateSchema,
    WorkerServicesUpdateSchema,
    WorkerAvailabilitySchema,
    WorkerLocationSchema,
    WorkerOnlineStatusSchema
)

class UserService:
    @staticmethod
    def get_user_profile(user: User, db: Optional[Session] = None) -> dict:
        services_list = []
        if user.offered_services:
            try:
                services_list = json.loads(user.offered_services)
            except Exception:
                services_list = user.offered_services.split(",")

        availability_data = {"days_off": [], "dead_slots": []}
        if user.availability:
            try:
                parsed_avail = json.loads(user.availability)
                if isinstance(parsed_avail, dict):
                    availability_data = {
                        "days_off": parsed_avail.get("days_off", []),
                        "dead_slots": parsed_avail.get("dead_slots", [])
                    }
            except Exception:
                pass

        location_data = None
        if db is not None:
            addr = (
                db.query(Address)
                .filter(Address.user_id == user.id)
                .order_by(desc(Address.is_default), desc(Address.id))
                .first()
            )
            if addr:
                location_data = {
                    "id": addr.id,
                    "area": addr.area,
                    "landmark": addr.landmark,
                    "city": addr.city,
                    "pincode": addr.pincode,
                    "full_address": addr.full_address,
                    "latitude": addr.latitude,
                    "longitude": addr.longitude,
                }

        return {
            "id": user.id,
            "phone_number": user.phone_number,
            "full_name": user.full_name,
            "role": user.role,
            "dob": user.dob,
            "gender": user.gender,
            "offered_services": services_list,
            "availability": availability_data,
            "location": location_data,
            "referral_code": user.referral_code,
            "referral_points": user.referral_points or 0,
            "referral_earnings": user.referral_earnings or 0,
            "is_online": bool(user.is_online != 0 if user.is_online is not None else True),
            "is_blocked": bool(getattr(user, "is_blocked", 0) or 0),
            "verification_status": user.verification_status or "approved",
            "profile_photo": user.profile_photo,
            "created_at": str(user.created_at) if user.created_at else None
        }

    @staticmethod
    def update_user_profile(data: UserUpdateSchema, current_user: User, db: Session) -> dict:
        if data.full_name is not None:
            current_user.full_name = data.full_name
        if data.dob is not None:
            current_user.dob = data.dob
        if data.gender is not None:
            current_user.gender = data.gender
            
        db.commit()
        db.refresh(current_user)
        
        return {
            "status": "success",
            "user": {
                "id": current_user.id,
                "phone_number": current_user.phone_number,
                "full_name": current_user.full_name,
                "role": current_user.role,
                "dob": current_user.dob,
                "gender": current_user.gender,
                "referral_code": current_user.referral_code,
                "referral_points": current_user.referral_points or 0,
            }
        }

    @staticmethod
    def update_worker_services(data: WorkerServicesUpdateSchema, current_user: User, db: Session) -> dict:
        from fastapi import HTTPException
        incoming = data.offered_services if isinstance(data.offered_services, list) else []
        # Preserve admin-locked prices: worker cannot change price/price_type on locked items.
        locked_by_id = {}
        if current_user.offered_services:
            try:
                stored = json.loads(current_user.offered_services)
                if isinstance(stored, list):
                    for item in stored:
                        if isinstance(item, dict) and item.get("id") and item.get("price_locked"):
                            locked_by_id[str(item["id"])] = item
            except Exception:
                pass
        merged = []
        for item in incoming:
            if isinstance(item, dict) and item.get("id"):
                sid = str(item["id"])
                if sid in locked_by_id:
                    locked = locked_by_id[sid]
                    item = dict(item)
                    for key in ("price", "price_type", "fixed_price", "price_per_hour"):
                        if key in locked:
                            item[key] = locked[key]
                    item["price_locked"] = True
                merged.append(item)
            else:
                merged.append(item)
        if isinstance(data.offered_services, list):
            current_user.offered_services = json.dumps(merged if merged else data.offered_services)
        else:
            current_user.offered_services = str(data.offered_services)

        db.commit()
        db.refresh(current_user)

        return {
            "status": "success",
            "message": "Offered services updated in database",
            "offered_services": merged if merged else data.offered_services
        }

    @staticmethod
    def update_worker_availability(data: WorkerAvailabilitySchema, current_user: User, db: Session) -> dict:
        payload = data.model_dump() if hasattr(data, "model_dump") else data.dict()
        current_user.availability = json.dumps(payload)
        db.commit()
        db.refresh(current_user)

        return {
            "status": "success",
            "message": "Worker availability and dead time zones updated in database",
            "availability": payload
        }

    @staticmethod
    def update_user_location(data: WorkerLocationSchema, current_user: User, db: Session) -> dict:
        addr = (
            db.query(Address)
            .filter(Address.user_id == current_user.id)
            .order_by(desc(Address.is_default), desc(Address.id))
            .first()
        )

        area_val = (data.area or "").strip() or "Nagpur"
        city_val = (data.city or "").strip() or "Nagpur"
        full_addr_val = (data.full_address or "").strip() or f"{area_val}, {city_val}"

        if not addr:
            addr = Address(
                user_id=current_user.id,
                tag="Operational",
                area=area_val,
                landmark=data.landmark,
                city=city_val,
                pincode=data.pincode,
                full_address=full_addr_val,
                latitude=str(data.latitude) if data.latitude is not None else None,
                longitude=str(data.longitude) if data.longitude is not None else None,
                is_default=1,
            )
            db.add(addr)
        else:
            addr.area = area_val
            addr.landmark = data.landmark
            addr.city = city_val
            addr.pincode = data.pincode
            addr.full_address = full_addr_val
            if data.latitude is not None:
                addr.latitude = str(data.latitude)
            if data.longitude is not None:
                addr.longitude = str(data.longitude)
            addr.is_default = 1

        db.commit()
        db.refresh(addr)

        return {
            "status": "success",
            "message": "Operational location updated successfully",
            "location": {
                "id": addr.id,
                "area": addr.area,
                "landmark": addr.landmark,
                "city": addr.city,
                "pincode": addr.pincode,
                "full_address": addr.full_address,
                "latitude": addr.latitude,
                "longitude": addr.longitude,
            }
        }

    @staticmethod
    def update_online_status(data: WorkerOnlineStatusSchema, current_user: User, db: Session) -> dict:
        current_user.is_online = 1 if data.is_online else 0
        db.commit()
        db.refresh(current_user)

        return {
            "status": "success",
            "message": f"Worker is now {'ONLINE' if data.is_online else 'OFFLINE'}",
            "is_online": bool(current_user.is_online != 0)
        }

