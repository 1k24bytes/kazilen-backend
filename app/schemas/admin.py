from pydantic import BaseModel
from typing import Optional


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminLoginResponse(BaseModel):
    status: str
    access_token: str
    token_type: str = "bearer"
    email: str


class AdminProfile(BaseModel):
    email: str
    role: str = "admin"


class AdminCreateWorkerRequest(BaseModel):
    phone_number: str
    full_name: str
    dob: Optional[str] = None
    gender: Optional[str] = None
    is_online: Optional[bool] = True


class AdminWorkerResponse(BaseModel):
    id: int
    phone_number: str
    full_name: Optional[str] = None
    role: str = "worker"
    dob: Optional[str] = None
    gender: Optional[str] = None
    referral_code: Optional[str] = None
    is_online: int = 1
    verification_status: str = "approved"
    profile_photo: Optional[str] = None

class AdminStats(BaseModel):
    total_users: int
    total_customers: int
    total_workers: int
    workers_online: int
    total_bookings: int
    bookings_pending: int
    bookings_completed: int
    total_reviews: int
    total_feedback: int = 0
    total_categories: int = 0
    total_sub_categories: int = 0
    total_referrals: int = 0
    total_membership_plans: int = 0
