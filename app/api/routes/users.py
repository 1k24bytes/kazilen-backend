from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.api.deps import get_current_user
from app.services.r2_service import R2Service
from app.schemas.user import (
    UserUpdateSchema,
    WorkerServicesUpdateSchema,
    WorkerAvailabilitySchema,
    WorkerLocationSchema,
    WorkerOnlineStatusSchema
)
from app.services.user_service import UserService

router = APIRouter()

@router.get("/me")
def read_users_me(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retrieves profile details of currently authenticated user."""
    return UserService.get_user_profile(current_user, db)

@router.put("/me")
def update_user_me(
    data: UserUpdateSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates profile information (full_name, dob, gender) for current user."""
    return UserService.update_user_profile(data, current_user, db)

@router.put("/me/services")
def update_worker_services(
    data: WorkerServicesUpdateSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates offered service IDs for a worker account."""
    return UserService.update_worker_services(data, current_user, db)

@router.put("/me/availability")
def update_worker_availability(
    data: WorkerAvailabilitySchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates weekly days off and daily dead time zones for a worker account."""
    return UserService.update_worker_availability(data, current_user, db)

@router.put("/me/location")
def update_worker_location(
    data: WorkerLocationSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates operational base/live location for current user or worker."""
    return UserService.update_user_location(data, current_user, db)

@router.post("/me/photo")
def upload_my_photo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Uploads the worker profile photo to Cloudflare R2 (single `file` field, Dunex pattern)."""
    if not file or not file.content_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A file is required in the \"file\" multipart field")
    contents = file.file.read()
    result = R2Service.upload_public_photo(contents, file.filename or "", file.content_type)
    current_user.profile_photo = result["url"]
    db.commit()
    db.refresh(current_user)
    return {"status": "success", "url": result["url"], "publicId": result["key"]}

@router.put("/me/online")
@router.put("/me/status")
def update_worker_online_status(
    data: WorkerOnlineStatusSchema,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Updates real-time online/offline dispatch status for a worker account."""
    return UserService.update_online_status(data, current_user, db)

