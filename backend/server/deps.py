from datetime import datetime, timezone
from typing import Annotated, Generator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from server.config import get_settings
from server.db import SessionLocal
from server.models import User
from server.security import decode_token

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _check_revocation(user: User, payload: dict) -> bool:
    """Return True if the token is still valid w.r.t. user.tokens_valid_from."""
    if user.tokens_valid_from:
        issued_at = datetime.fromtimestamp(payload.get("iat", 0), tz=timezone.utc)
        if issued_at < user.tokens_valid_from.replace(tzinfo=timezone.utc):
            return False
    return True


def resolve_user_from_token(db: Session, token: str) -> Optional[User]:
    """Validate an access token string and return the User (or None).

    Enforces type=="access" and token revocation. Used by the ForwardAuth endpoint.
    """
    payload = decode_token(token)
    if not payload:
        return None
    if payload.get("type") != "access":
        return None
    sub = payload.get("sub")
    if sub is None:
        return None
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if not user:
        return None
    if not _check_revocation(user, payload):
        return None
    return user


def _get_current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    settings = get_settings()

    token: Optional[str] = None
    if credentials:
        token = credentials.credentials
    else:
        token = request.cookies.get(settings.cookie_session_name)

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    if not _check_revocation(user, payload):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token revoked")

    return user


def _get_admin_user(user: Annotated[User, Depends(_get_current_user)]) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required")
    return user


get_current_user = _get_current_user

CurrentUser = Annotated[User, Depends(_get_current_user)]
AdminUser = Annotated[User, Depends(_get_admin_user)]
DbSession = Annotated[Session, Depends(get_db)]
