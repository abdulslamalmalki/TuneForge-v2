from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import config


def _request_context(request: Request) -> dict[str, object]:
    return {
        "app_version": config.APP_VERSION,
        "current_user": getattr(request.state, "user", None),
        "authentication_setup_required": getattr(request.state, "setup_required", False),
        "authentication_enabled": config.AUTH_MODE == "required",
    }


templates = Jinja2Templates(
    directory=Path(__file__).parent / "templates",
    context_processors=[_request_context],
)
