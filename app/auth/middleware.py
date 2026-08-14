"""Application-wide identity, route policy, and browser security headers."""

from urllib.parse import quote, urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app import config
from app.auth.context import reset_actor, set_actor
from app.db.session import SessionLocal
from app.domain import auth
from app.security import rate_limit

_PUBLIC_GET_EXACT = {
    "/",
    "/answer",
    "/compare",
    "/library",
    "/upload-manual",
    "/login",
    "/setup",
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/interface-contract",
    "/api/v1/interface/presentation",
    "/api/v1/interface/questions",
    "/api/v1/interface/route",
}
_PUBLIC_GET_PREFIXES = (
    "/static/",
    "/library/",
    "/api/v1/interface/questions/",
    "/api/v1/library/",
)
_PUBLIC_POST_EXACT = {
    "/answer",
    "/upload-manual",
    "/login",
    "/setup",
    "/api/v1/interface/trusted-web-research",
}
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class AuthBoundaryMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self._rate_limiter = rate_limit.SlidingWindowRateLimiter()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if config.AUTH_MODE == "disabled":
            principal = auth.LOCAL_PRINCIPAL
            setup_required = False
        else:
            with SessionLocal() as session:
                principal = auth.principal_for_token(
                    session, request.cookies.get(config.AUTH_COOKIE_NAME)
                )
                setup_required = auth.user_count(session) == 0
        request.state.user = principal
        request.state.setup_required = setup_required

        policy_response = self._policy_response(request, principal, setup_required)
        if policy_response is not None:
            return self._secure(
                policy_response,
                request=request,
                protected=not self._is_public(request.method, request.url.path),
            )

        rule = rate_limit.rule_for_request(request)
        decision = (
            self._rate_limiter.check(rule, rate_limit.request_identity(request, principal))
            if rule is not None
            else None
        )
        if decision is not None and not decision.allowed:
            response = self._rate_limited(request)
            rate_limit.apply_headers(response.headers, decision)
            return self._secure(
                response,
                request=request,
                protected=not self._is_public(request.method, request.url.path),
            )

        actor_token = set_actor(principal.username if principal else "anonymous")
        try:
            response = await call_next(request)
        finally:
            reset_actor(actor_token)
        if decision is not None:
            rate_limit.apply_headers(response.headers, decision)
        return self._secure(
            response,
            request=request,
            protected=not self._is_public(request.method, request.url.path),
        )

    def _policy_response(
        self,
        request: Request,
        principal: auth.Principal | None,
        setup_required: bool,
    ) -> Response | None:
        if config.AUTH_MODE == "disabled":
            return None
        path = request.url.path
        if request.method in _UNSAFE_METHODS and not _same_origin(request):
            return self._forbidden(request, "Same-origin confirmation is required.")
        if self._is_public(request.method, path):
            return None
        if setup_required:
            if path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Authentication setup is required."}, status_code=503
                )
            return RedirectResponse(url=f"/setup?next={_next_path(request)}", status_code=303)
        if principal is None:
            if path.startswith("/api/"):
                return JSONResponse(
                    {"detail": "Authentication required."},
                    status_code=401,
                    headers={"WWW-Authenticate": "Cookie"},
                )
            return RedirectResponse(url=f"/login?next={_next_path(request)}", status_code=303)
        return None

    @staticmethod
    def _is_public(method: str, path: str) -> bool:
        if method == "GET":
            return path in _PUBLIC_GET_EXACT or path.startswith(_PUBLIC_GET_PREFIXES)
        return method == "POST" and path in _PUBLIC_POST_EXACT

    @staticmethod
    def _forbidden(request: Request, message: str) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=403)
        return Response(message, status_code=403, media_type="text/plain")

    @staticmethod
    def _rate_limited(request: Request) -> Response:
        message = "Too many requests. Try again later."
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": message}, status_code=429)
        return Response(message, status_code=429, media_type="text/plain")

    @staticmethod
    def _secure(response: Response, *, request: Request, protected: bool) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'none'; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'",
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if config.ENVIRONMENT == "production":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if protected or request.url.path in {"/answer", "/login", "/setup", "/upload-manual"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin", "")
    if origin:
        parsed = urlsplit(origin)
        return parsed.scheme == request.url.scheme and parsed.netloc == request.url.netloc
    # Non-browser API clients cannot send browser Origin reliably. Requiring a
    # non-simple custom header keeps cross-origin forms from invoking mutations.
    return (
        request.url.path.startswith("/api/") and request.headers.get("x-tuneforge-request") == "1"
    )


def _next_path(request: Request) -> str:
    value = request.url.path
    if request.url.query:
        value += "?" + request.url.query
    return quote(value, safe="")
