from sqlite3 import IntegrityError

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from app.core.config import settings
from app.core.security import validate_origin
from app.services.accounts import SESSION_COOKIE, authenticate, create_account, create_session, revoke_session


def require_local_auth(request: Request) -> None:
    if settings.app_env != "dev":
        raise HTTPException(404, "Account sign-in is unavailable through this service.")
    validate_origin(request)


router = APIRouter(prefix="/auth", tags=["Authentication"], dependencies=[Depends(require_local_auth)])


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


def _set_session(request: Request, response: Response, account_id: str) -> dict[str, bool]:
    token = create_session(account_id, request.cookies.get(SESSION_COOKIE))
    response.set_cookie(SESSION_COOKIE, token, max_age=settings.session_minutes * 60, path="/",
                        httponly=True, secure=request.url.scheme == "https", samesite="strict")
    return {"ok": True}


@router.post("/signup", status_code=201)
def signup(body: Credentials, request: Request, response: Response) -> dict[str, bool]:
    try:
        account_id = create_account(body.email, body.password.get_secret_value())
    except IntegrityError:
        raise HTTPException(409, "Could not create this account. Try signing in.") from None
    except ValueError:
        raise HTTPException(422, "Enter a valid email address and a password of at least 12 characters.") from None
    return _set_session(request, response, account_id)


@router.post("/login")
def login(body: Credentials, request: Request, response: Response) -> dict[str, bool]:
    account_id = authenticate(body.email, body.password.get_secret_value())
    if account_id is None:
        raise HTTPException(401, "Email or password is incorrect.")
    return _set_session(request, response, account_id)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True,
                           secure=request.url.scheme == "https", samesite="strict")
    return {"ok": True}
