from datetime import datetime
from pydantic import BaseModel
from typing import Optional, List, Union, Dict, Any

class UserUpdateSchema(BaseModel):
    full_name: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None

class WorkerServicesUpdateSchema(BaseModel):
    offered_services: Union[List[Union[Dict[str, Any], str]], str, Dict[str, Any]]

class DeadSlotSchema(BaseModel):
    start_time: str  # e.g. "13:00"
    end_time: str    # e.g. "15:00"
    label: Optional[str] = "Break"

class WorkerAvailabilitySchema(BaseModel):
    days_off: List[str] = []  # e.g. ["Sunday", "Friday"]
    dead_slots: List[DeadSlotSchema] = []

class WorkerLocationSchema(BaseModel):
    area: str
    landmark: Optional[str] = None
    city: str = "Nagpur"
    pincode: Optional[str] = None
    full_address: str
    latitude: Optional[Union[str, float]] = None
    longitude: Optional[Union[str, float]] = None

class WorkerOnlineStatusSchema(BaseModel):
    is_online: bool

class UserResponseSchema(BaseModel):
    id: int
    phone_number: str
    full_name: Optional[str] = None
    role: str
    dob: Optional[str] = None
    gender: Optional[str] = None
    offered_services: Union[List[Any], str] = []
    availability: Optional[Dict[str, Any]] = None
    location: Optional[Dict[str, Any]] = None
    referral_code: Optional[str] = None
    referral_points: int = 0
    is_online: bool = True
    verification_status: str = "approved"
    profile_photo: Optional[str] = None
    created_at: Optional[Union[datetime, str]] = None
