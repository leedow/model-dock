import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from sqlalchemy import select

from modeldock.entities import APIKey, Grant, User, WebSession
from modeldock.errors import DockError

hasher = PasswordHasher()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def verify_password(encoded: str, plain: str) -> bool:
    try:
        return hasher.verify(encoded, plain)
    except (VerificationError, InvalidHashError):
        return False


@dataclass
class Principal:
    user_id: str
    username: str
    role: str
    model_ids: set[str]
    key_id: str | None = None
    csrf: str | None = None

    def permits(self, model_id: str) -> bool:
        return (self.role == "admin" and self.key_id is None) or model_id in self.model_ids


async def authenticate(request: Request) -> Principal:
    now = time.time()
    authorization = request.headers.get("authorization", "")
    async with request.app.state.sessions() as session:
        key = None
        csrf = None
        if authorization:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token:
                raise DockError(
                    "Invalid Bearer credentials", "invalid_api_key", 401, error_type="authentication_error"
                )
            key = await session.scalar(select(APIKey).where(APIKey.token_hash == digest(token)))
            if not key or key.revoked or (key.expires_at and key.expires_at <= now):
                raise DockError(
                    "Invalid or expired API key", "invalid_api_key", 401, error_type="authentication_error"
                )
            user_id = key.user_id
        else:
            cookie = request.cookies.get("modeldock_session", "")
            web = await session.get(WebSession, digest(cookie)) if cookie else None
            if web is None or web.expires_at <= now:
                raise DockError("Please sign in", "unauthenticated", 401, error_type="authentication_error")
            user_id, csrf = web.user_id, web.csrf
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                supplied = request.headers.get("x-csrf-token", "")
                if not secrets.compare_digest(supplied, csrf):
                    raise DockError("Invalid CSRF token", "csrf_failed", 403)
        user = await session.get(User, user_id)
        if user is None or not user.enabled:
            raise DockError("Account disabled", "account_disabled", 401, error_type="authentication_error")
        grants = set(await session.scalars(select(Grant.model_id).where(Grant.user_id == user_id)))
        if user.role == "admin":
            from modeldock.entities import Model

            grants = set(await session.scalars(select(Model.id)))
        if key:
            grants &= set(key.model_ids)
            # Atomic fixed-window counter. A single Lua call cannot leave immortal counters.
            count = await request.app.state.redis.eval(
                "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
                1,
                f"modeldock:rate:{key.id}:{int(now // 60)}",
            )
            if count > key.rpm:
                raise DockError(
                    "API key request limit exceeded", "rate_limit", 429, error_type="rate_limit_error"
                )
        principal = Principal(user.id, user.username, user.role, grants, key.id if key else None, csrf)
        request.state.principal = principal
        return principal


async def web_user(principal: Principal = Depends(authenticate)) -> Principal:
    if principal.key_id:
        raise DockError("Management endpoints require a web session", "forbidden", 403)
    return principal


async def admin(principal: Principal = Depends(web_user)) -> Principal:
    if principal.role != "admin":
        raise DockError("Administrator access required", "forbidden", 403)
    return principal


def public_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "enabled": user.enabled,
        "created_at": user.created_at,
    }
