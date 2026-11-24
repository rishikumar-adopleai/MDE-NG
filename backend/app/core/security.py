from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings
from app.services.accounts import SESSION_COOKIE, session_account

ALGORITHM = "HS256"
bearer = HTTPBearer(auto_error=False)


class CurrentUser(BaseModel):
    user_id: str
    display_name: str
    role: str


def validate_origin(request: Request) -> None:
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site == "same-origin":
        return
    origin = request.headers.get("origin")
    allowed_origins = {str(request.base_url).rstrip("/"), settings.frontend_origin}
    if fetch_site == "cross-site" or (origin is not None and origin not in allowed_origins):
        raise HTTPException(403, "Open the workspace directly and try again.")


def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> CurrentUser:
    if settings.app_env == "dev":
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            validate_origin(request)
        token = request.cookies.get(SESSION_COOKIE)
        account = session_account(token) if token else None
        if account is None:
            raise HTTPException(401, "Sign in to continue.")
        return CurrentUser(user_id=account["id"], display_name="Workspace user", role=account["role"])
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
    try:
        claims = jwt.decode(credentials.credentials, settings.jwt_secret,
                            algorithms=[ALGORITHM], audience=settings.jwt_audience, issuer=settings.jwt_issuer,
                            options={"require_exp": True, "require_sub": True})
        return CurrentUser(user_id=claims["sub"], display_name=claims["name"], role=claims["role"])
    except (JWTError, KeyError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token.") from exc


def require_roles(*roles: str):
    def dependency(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role != "admin" and user.role not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This demo role cannot perform that action.")
        return user
    return dependency
