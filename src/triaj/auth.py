"""Bearer token doğrulaması. Token değeri hiçbir yanıtta ya da logda yer almaz."""

from __future__ import annotations

import secrets as _secrets

from fastapi import HTTPException, Request, status


def require_token(request: Request) -> None:
    settings = request.app.state.settings
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Bearer token gerekli", headers={"WWW-Authenticate": "Bearer"}
        )
    token = token.strip()
    ok = False
    for t in settings.tokens:
        if _secrets.compare_digest(t.encode(), token.encode()):
            ok = True
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "geçersiz token", headers={"WWW-Authenticate": "Bearer"}
        )
