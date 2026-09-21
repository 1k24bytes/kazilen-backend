"""Cloudflare R2 object storage (S3-compatible API via boto3).

Mirrors the Dunex upload pattern (`backend-dunex/src/config/r2.js`):
- Single `file` multipart field, sanitized `folder/<uuid>.<ext>` object keys.
- Public bucket uploads return a directly usable `url`.
- 503 when R2 env vars are missing; images-only + 5 MB hardening.
"""
import re
import uuid

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core.config import settings

ALLOWED_PHOTO_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_PHOTO_BYTES = 5 * 1024 * 1024
PHOTO_FOLDER = "workers"


def _r2_client():
    if not (settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Photo upload is not configured (missing R2 credentials).",
        )
    return boto3.client(
        "s3",
        region_name="auto",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
    )


def _safe_extension(filename: str, content_type: str) -> str:
    ext = ""
    if filename and "." in filename:
        ext = filename.rsplit(".", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9]+", ext or ""):
        ext = (content_type or "").split("/")[-1].lower()
    if not re.fullmatch(r"[a-z0-9]+", ext or ""):
        ext = "jpg"
    return ext


class R2Service:
    @staticmethod
    def upload_public_photo(file_bytes: bytes, filename: str, content_type: str, folder: str = PHOTO_FOLDER) -> dict:
        """Uploads a profile photo to the public R2 bucket. Returns {key, url}."""
        if content_type not in ALLOWED_PHOTO_MIME_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only JPG, PNG or WebP photos are allowed.",
            )
        if not file_bytes or len(file_bytes) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Photo must be non-empty and under 5 MB.",
            )
        if not settings.R2_PUBLIC_BUCKET_NAME:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Photo upload is not configured (missing R2 bucket).",
            )
        if not settings.R2_PUBLIC_URL:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Photo upload is not configured (missing R2 public URL).",
            )

        safe_folder = re.sub(r"[^a-zA-Z0-9/_-]", "", (folder or "").strip()).strip("/") or PHOTO_FOLDER
        key = f"public/{safe_folder}/{uuid.uuid4()}.{_safe_extension(filename, content_type)}"

        try:
            _r2_client().put_object(
                Bucket=settings.R2_PUBLIC_BUCKET_NAME,
                Key=key,
                Body=file_bytes,
                ContentLength=len(file_bytes),
                ContentType=content_type,
                ContentDisposition="inline",
            )
        except (BotoCoreError, ClientError) as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Photo upload failed: {e}",
            )
        return {"key": key, "url": f"{settings.R2_PUBLIC_URL}/{key}"}
