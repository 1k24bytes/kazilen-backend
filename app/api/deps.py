from fastapi import Depends, HTTPException, Request, status
from fastapi.security.utils import get_authorization_scheme_param
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.db.models import User
from app.core.config import settings
from app.core.security import decode_access_token

COOKIE_NAME = "access_token"
ADMIN_COOKIE_NAME = "admin_token"

def get_current_user(
    request: Request,
    db: Session = Depends(get_db)
) -> User:
    """Dependency to retrieve and validate the authenticated user.

    Reads the JWT from the HttpOnly `access_token` cookie first,
    falling back to the `Authorization: Bearer <token>` header for
    backward compatibility (e.g. API clients / tests).
    """
    token = request.cookies.get(COOKIE_NAME)

    if not token:
        scheme, param = get_authorization_scheme_param(request.headers.get("Authorization"))
        if scheme.lower() == "bearer":
            token = param

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials"
        )
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )
    return user


def get_current_admin(request: Request) -> dict:
    """Dependency to validate the admin-panel JWT.

    Reads the ``admin_token`` HttpOnly cookie first, falling back to the
    ``Authorization: Bearer <token>`` header (used by the Vite admin SPA
    which keeps the token in memory/localStorage).
    The token must carry ``role == "admin"`` and ``sub == ADMIN_EMAIL``.
    """
    token = request.cookies.get(ADMIN_COOKIE_NAME)

    if not token:
        scheme, param = get_authorization_scheme_param(request.headers.get("Authorization"))
        if scheme.lower() == "bearer":
            token = param

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if payload is None or payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate admin credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    email: str | None = payload.get("sub")
    if not email or email.strip().lower() != settings.ADMIN_EMAIL.strip().lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access denied",
        )
    return {"email": email, "role": "admin"}
