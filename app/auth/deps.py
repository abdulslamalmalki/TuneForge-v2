from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.domain.auth import ROLE_ADMINISTRATOR, Principal


def require_principal(request: Request) -> Principal:
    principal = getattr(request.state, "user", None)
    if principal is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return principal


def require_administrator(request: Request) -> Principal:
    principal = require_principal(request)
    if principal.role != ROLE_ADMINISTRATOR:
        raise HTTPException(status_code=403, detail="Administrator access is required.")
    return principal


CurrentPrincipal = Annotated[Principal, Depends(require_principal)]
AdministratorPrincipal = Annotated[Principal, Depends(require_administrator)]
