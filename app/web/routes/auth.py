"""First-run setup, curator sessions, and administrator account management."""

from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import config
from app.auth.deps import AdministratorPrincipal
from app.db.session import get_session
from app.domain import auth
from app.web.templating import templates

router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, session: SessionDep, next: str = "") -> HTMLResponse:
    if config.AUTH_MODE == "disabled":
        return RedirectResponse("/admin", status_code=303)
    if auth.user_count(session):
        return RedirectResponse(f"/login?next={quote(_safe_next(next), safe='')}", status_code=303)
    return templates.TemplateResponse(
        request,
        "auth_setup.html",
        {"next": _safe_next(next), "error": None, "values": {}},
    )


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    session: SessionDep,
    username: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse:
    destination = _safe_next(next)
    try:
        if password != password_confirm:
            raise auth.AuthError("Passwords do not match.")
        user = auth.create_first_administrator(
            session,
            username=username,
            display_name=display_name,
            password=password,
        )
        token = auth.create_session(session, user)
    except auth.AuthError as error:
        return templates.TemplateResponse(
            request,
            "auth_setup.html",
            {
                "next": destination,
                "error": error.message,
                "values": {"username": username, "display_name": display_name},
            },
            status_code=400,
        )
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(response, token)
    return response


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, session: SessionDep, next: str = "") -> HTMLResponse:
    destination = _safe_next(next)
    if config.AUTH_MODE == "disabled":
        return RedirectResponse(destination, status_code=303)
    if auth.user_count(session) == 0:
        return RedirectResponse(f"/setup?next={quote(destination, safe='')}", status_code=303)
    if request.state.user is not None:
        return RedirectResponse(destination, status_code=303)
    return templates.TemplateResponse(
        request,
        "auth_login.html",
        {"next": destination, "error": None, "username": ""},
    )


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    session: SessionDep,
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "",
) -> HTMLResponse:
    destination = _safe_next(next)
    user = auth.authenticate(session, username, password)
    if user is None:
        return templates.TemplateResponse(
            request,
            "auth_login.html",
            {
                "next": destination,
                "error": "Username or password is incorrect.",
                "username": username,
            },
            status_code=400,
        )
    token = auth.create_session(session, user)
    response = RedirectResponse(destination, status_code=303)
    _set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request, session: SessionDep) -> RedirectResponse:
    principal = request.state.user
    auth.revoke_session(
        session,
        request.cookies.get(config.AUTH_COOKIE_NAME),
        actor=principal.username,
    )
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie(config.AUTH_COOKIE_NAME, path="/")
    return response


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    session: SessionDep,
    administrator: AdministratorPrincipal,
    done: str = "",
) -> HTMLResponse:
    return _render_users(
        request,
        session,
        administrator,
        message={
            "created": "Account created.",
            "disabled": "Account disabled and its sessions revoked.",
            "password": "Password reset and existing sessions revoked.",
        }.get(done),
    )


@router.post("/admin/users", response_class=HTMLResponse)
def create_user(
    request: Request,
    session: SessionDep,
    administrator: AdministratorPrincipal,
    username: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    role: Annotated[str, Form()] = auth.ROLE_REVIEWER,
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        if password != password_confirm:
            raise auth.AuthError("Passwords do not match.")
        auth.create_user(
            session,
            username=username,
            display_name=display_name,
            password=password,
            role=role,
            actor=administrator.username,
        )
    except auth.AuthError as error:
        return _render_users(
            request,
            session,
            administrator,
            error=error.message,
            values={"username": username, "display_name": display_name, "role": role},
            status_code=400,
        )
    return RedirectResponse("/admin/users?done=created", status_code=303)


@router.post("/admin/users/{user_id}/disable", response_class=HTMLResponse)
def disable_user(
    request: Request,
    user_id: int,
    session: SessionDep,
    administrator: AdministratorPrincipal,
) -> HTMLResponse:
    try:
        auth.disable_user(session, user_id, actor=administrator)
    except auth.AuthError as error:
        return _render_users(request, session, administrator, error=error.message, status_code=400)
    return RedirectResponse("/admin/users?done=disabled", status_code=303)


@router.post("/admin/users/{user_id}/password", response_class=HTMLResponse)
def reset_password(
    request: Request,
    user_id: int,
    session: SessionDep,
    administrator: AdministratorPrincipal,
    password: Annotated[str, Form()] = "",
    password_confirm: Annotated[str, Form()] = "",
) -> HTMLResponse:
    try:
        if password != password_confirm:
            raise auth.AuthError("Passwords do not match.")
        auth.reset_password(session, user_id, password=password, actor=administrator)
    except auth.AuthError as error:
        return _render_users(request, session, administrator, error=error.message, status_code=400)
    response = RedirectResponse("/admin/users?done=password", status_code=303)
    if administrator.user_id == user_id:
        response.delete_cookie(config.AUTH_COOKIE_NAME, path="/")
        response.headers["location"] = "/login"
    return response


def _render_users(
    request: Request,
    session: Session,
    administrator: auth.Principal,
    *,
    message: str | None = None,
    error: str | None = None,
    values: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    users = auth.list_users(session)
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "user_by_id": {user.id: user for user in users},
            "events": auth.list_events(session),
            "administrator": administrator,
            "message": message,
            "error": error,
            "values": values or {},
            "roles": auth.ROLES,
        },
        status_code=status_code,
    )


def _safe_next(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if not value.startswith("/") or value.startswith("//") or parsed.scheme or parsed.netloc:
        return "/admin"
    return value


def _set_session_cookie(response: RedirectResponse, token: str) -> None:
    response.set_cookie(
        config.AUTH_COOKIE_NAME,
        token,
        max_age=config.AUTH_SESSION_HOURS * 60 * 60,
        httponly=True,
        secure=config.AUTH_COOKIE_SECURE,
        samesite="strict",
        path="/",
    )
