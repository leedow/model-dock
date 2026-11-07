import asyncio
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from modeldock.adapters import REGISTRY, descriptors, operations, validate_adapter
from modeldock.entities import APIKey, CallLog, Grant, Model, Outbox, Task, User, WebSession
from modeldock.errors import DockError
from modeldock.schemas import KeyInput, LoginInput, ModelInput, PasswordInput, UserCreate, UserUpdate
from modeldock.security import Principal, admin, digest, hasher, public_user, verify_password, web_user
from modeldock.services.providers import lock_model, validate_binding

router = APIRouter(prefix="/api/v1", tags=["Management"])


def model_public(model: Model) -> dict[str, Any]:
    return {
        **{column.name: getattr(model, column.name) for column in Model.__table__.columns},
        "operations": operations(model.adapter, model.config),
    }


def key_public(key: APIKey) -> dict[str, Any]:
    return {
        "id": key.id,
        "name": key.name,
        "prefix": key.prefix,
        "model_ids": key.model_ids,
        "rpm": key.rpm,
        "expires_at": key.expires_at,
        "revoked": key.revoked,
        "created_at": key.created_at,
    }


@router.post("/auth/login")
async def login(body: LoginInput, request: Request, response: Response):
    redis = request.app.state.redis
    remote = request.client.host if request.client else "unknown"
    bucket = f"modeldock:login:{digest(remote)}:{int(time.time() // 60)}"
    count = await redis.eval(
        "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
        1,
        bucket,
    )
    if count > 20:
        raise DockError("Too many login attempts", "rate_limit", 429, error_type="rate_limit_error")
    async with request.app.state.sessions() as session:
        user = await session.scalar(select(User).where(User.username == body.username))
        valid = user and await asyncio.to_thread(verify_password, user.password_hash, body.password)
        if not valid or not user or not user.enabled:
            raise DockError(
                "Invalid username or password", "invalid_credentials", 401, error_type="authentication_error"
            )
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
        existing = request.cookies.get("modeldock_session")
        if existing:
            await session.execute(delete(WebSession).where(WebSession.token_hash == digest(existing)))
        session.add(
            WebSession(
                token_hash=digest(token),
                user_id=user.id,
                csrf=csrf,
                expires_at=time.time() + request.app.state.settings.session_hours * 3600,
            )
        )
        await session.commit()
        response.set_cookie(
            "modeldock_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.app.state.settings.secure_cookie,
            max_age=request.app.state.settings.session_hours * 3600,
        )
        return {"user": public_user(user), "csrf_token": csrf}


@router.get("/auth/me")
async def me(principal: Principal = Depends(web_user)):
    return {
        "user": {"id": principal.user_id, "username": principal.username, "role": principal.role},
        "csrf_token": principal.csrf,
    }


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response, principal: Principal = Depends(web_user)):
    async with request.app.state.sessions() as session:
        await session.execute(
            delete(WebSession).where(
                WebSession.token_hash == digest(request.cookies.get("modeldock_session", ""))
            )
        )
        await session.commit()
    response.delete_cookie("modeldock_session")


@router.post("/auth/password", status_code=204)
async def password(
    body: PasswordInput, request: Request, response: Response, principal: Principal = Depends(web_user)
):
    async with request.app.state.sessions() as session:
        user = await session.get(User, principal.user_id)
        if not await asyncio.to_thread(verify_password, user.password_hash, body.current_password):
            raise DockError("Current password is incorrect", "invalid_password", 400)
        user.password_hash = await asyncio.to_thread(hasher.hash, body.new_password)
        await session.execute(delete(WebSession).where(WebSession.user_id == user.id))
        await session.commit()
    response.delete_cookie("modeldock_session")


@router.get("/models")
async def models(request: Request, principal: Principal = Depends(web_user)):
    async with request.app.state.sessions() as session:
        query = select(Model).order_by(Model.created_at)
        if principal.role != "admin":
            query = query.where(Model.id.in_(principal.model_ids))
        return {"data": [model_public(m) for m in await session.scalars(query)]}


@router.get("/adapters")
async def adapters(principal: Principal = Depends(web_user)):
    return {"data": descriptors()}


@router.post("/models", status_code=201)
async def create_model(body: ModelInput, request: Request, principal: Principal = Depends(admin)):
    if getattr(REGISTRY.get(body.adapter), "remote", False):
        body.device, body.memory_mb = "cpu", 0
    async with request.app.state.sessions() as session:
        await validate_binding(session, body)
        model = Model(**body.model_dump())
        session.add(model)
        try:
            await session.commit()
        except IntegrityError:
            raise DockError("Model name already exists", "duplicate_model", 409) from None
        return model_public(model)


@router.put("/models/{model_id}")
async def update_model(
    model_id: str, body: ModelInput, request: Request, principal: Principal = Depends(admin)
):
    if getattr(REGISTRY.get(body.adapter), "remote", False):
        body.device, body.memory_mb = "cpu", 0
    async with request.app.state.sessions() as session:
        model = await session.get(Model, model_id)
        if not model:
            raise DockError("Model not found", "not_found", 404)
        model = await lock_model(session, model, body.provider_connection_id)
        await validate_binding(session, body)
        busy = await session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.model_id == model_id, Task.status.in_(["queued", "running"]))
        )
        if model.state != "unloaded" or busy:
            raise DockError("Unload model and finish pending tasks before editing", "model_busy", 409)
        for key, value in body.model_dump().items():
            setattr(model, key, value)
        try:
            await session.commit()
        except IntegrityError:
            raise DockError("Model name already exists", "duplicate_model", 409) from None
        return model_public(model)


@router.post("/models/{model_id}/{action}", status_code=202)
async def model_action(model_id: str, action: str, request: Request, principal: Principal = Depends(admin)):
    if action not in {"enable", "disable", "load", "unload"}:
        raise DockError("Unknown action", "not_found", 404)
    async with request.app.state.sessions() as session:
        model = await session.get(Model, model_id)
        if not model:
            raise DockError("Model not found", "not_found", 404)
        model = await lock_model(session, model)
        if action == "unload" and model.state == "loading":
            raise DockError("Model is loading; wait for completion", "model_busy", 409)
        if action in {"enable", "load"}:
            validate_adapter(model.adapter, model.capability, model.config, True)
        if action == "enable":
            model.enabled = True
        elif action == "disable":
            model.enabled = False
            session.add(Outbox(kind="drain", target_id=model_id))
        elif action == "load":
            if not model.enabled:
                raise DockError("Enable the model first", "model_disabled", 409)
            if model.state not in {"ready", "loading"}:
                model.state = "loading"
                session.add(Outbox(kind="load", target_id=model_id))
        else:
            if model.active:
                raise DockError("Model has in-flight requests", "model_busy", 409)
            session.add(Outbox(kind="unload", target_id=model_id))
        await session.commit()
        return model_public(model)


@router.get("/keys")
async def keys(request: Request, principal: Principal = Depends(web_user)):
    async with request.app.state.sessions() as session:
        return {
            "data": [
                key_public(k)
                for k in await session.scalars(
                    select(APIKey)
                    .where(APIKey.user_id == principal.user_id)
                    .order_by(APIKey.created_at.desc())
                )
            ]
        }


@router.post("/keys", status_code=201)
async def create_key(body: KeyInput, request: Request, principal: Principal = Depends(web_user)):
    if not set(body.model_ids) <= principal.model_ids:
        raise DockError("Key grants exceed your model access", "forbidden", 403)
    if body.expires_at is not None and body.expires_at <= time.time():
        raise DockError("Expiration must be in the future", "invalid_expiration")
    token = "mdk_" + secrets.token_urlsafe(36)
    async with request.app.state.sessions() as session:
        key = APIKey(
            user_id=principal.user_id,
            name=body.name,
            prefix=token[:12],
            token_hash=digest(token),
            model_ids=list(set(body.model_ids)),
            rpm=body.rpm,
            expires_at=body.expires_at,
        )
        session.add(key)
        await session.commit()
        return {**key_public(key), "secret": token}


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: str, request: Request, principal: Principal = Depends(web_user)):
    async with request.app.state.sessions() as session:
        key = await session.get(APIKey, key_id)
        if not key or key.user_id != principal.user_id:
            raise DockError("Key not found", "not_found", 404)
        key.revoked = True
        await session.commit()


@router.get("/users")
async def users(request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        grants: dict[str, list[str]] = {}
        for grant in await session.scalars(select(Grant)):
            grants.setdefault(grant.user_id, []).append(grant.model_id)
        return {
            "data": [
                {**public_user(u), "model_ids": grants.get(u.id, [])}
                for u in await session.scalars(select(User).order_by(User.created_at))
            ]
        }


async def replace_grants(session: Any, user_id: str, model_ids: list[str]) -> None:
    valid = set(await session.scalars(select(Model.id).where(Model.id.in_(model_ids))))
    if valid != set(model_ids):
        raise DockError("One or more models do not exist", "invalid_model")
    await session.execute(delete(Grant).where(Grant.user_id == user_id))
    for model_id in valid:
        session.add(Grant(user_id=user_id, model_id=model_id))


@router.post("/users", status_code=201)
async def create_user(body: UserCreate, request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        if await session.scalar(select(User).where(User.username == body.username)):
            raise DockError("Username already exists", "duplicate_user", 409)
        user = User(
            username=body.username,
            password_hash=await asyncio.to_thread(hasher.hash, body.password),
            role=body.role,
        )
        session.add(user)
        await session.flush()
        await replace_grants(session, user.id, body.model_ids)
        await session.commit()
        return public_user(user)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str, body: UserUpdate, request: Request, principal: Principal = Depends(admin)
):
    if user_id == principal.user_id and body.enabled is False:
        raise DockError("Cannot disable your own account", "self_disable", 409)
    async with request.app.state.sessions() as session:
        user = await session.get(User, user_id)
        if not user:
            raise DockError("User not found", "not_found", 404)
        if body.enabled is not None:
            user.enabled = body.enabled
        if body.password:
            user.password_hash = await asyncio.to_thread(hasher.hash, body.password)
        if body.model_ids is not None:
            await replace_grants(session, user_id, body.model_ids)
        if body.enabled is False:
            await session.execute(update(APIKey).where(APIKey.user_id == user_id).values(revoked=True))
        if body.password or body.enabled is False:
            await session.execute(delete(WebSession).where(WebSession.user_id == user_id))
        await session.commit()
        return public_user(user)


@router.get("/logs")
async def logs(
    request: Request,
    model: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    offset: int = 0,
    limit: int = 50,
    principal: Principal = Depends(web_user),
):
    if offset < 0 or not 1 <= limit <= 200:
        raise DockError("Invalid pagination", "invalid_pagination")
    query = select(CallLog)
    if principal.role != "admin":
        query = query.where(CallLog.user_id == principal.user_id)
    if model:
        query = query.where(CallLog.model == model)
    if status:
        query = query.where(CallLog.status == status)
    if request_id:
        query = query.where(CallLog.request_id == request_id)
    if since is not None:
        query = query.where(CallLog.created_at >= since)
    if until is not None:
        query = query.where(CallLog.created_at <= until)
    async with request.app.state.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        entries = await session.scalars(query.order_by(CallLog.created_at.desc()).offset(offset).limit(limit))
        return {
            "total": total,
            "data": [{c.name: getattr(log, c.name) for c in CallLog.__table__.columns} for log in entries],
        }


@router.get("/overview")
async def overview(request: Request, principal: Principal = Depends(web_user)):
    async with request.app.state.sessions() as session:
        logs_query = select(CallLog)
        tasks_query = select(Task)
        model_query = select(Model)
        if principal.role != "admin":
            logs_query = logs_query.where(CallLog.user_id == principal.user_id)
            tasks_query = tasks_query.where(Task.user_id == principal.user_id)
            model_query = model_query.where(Model.id.in_(principal.model_ids))
        calls = list(await session.scalars(logs_query.where(CallLog.created_at > time.time() - 86400)))
        task_rows = list(await session.scalars(tasks_query.where(Task.status.in_(["queued", "running"]))))
        model_rows = list(await session.scalars(model_query))
    runtime = await request.app.state.runtime.snapshot()
    now = time.time()
    activity = [
        sum(now - (10 - i) * 8640 <= c.created_at < now - (9 - i) * 8640 for c in calls) for i in range(10)
    ]
    return {
        "activity": activity,
        "health": {"database": True, "queue": True, "worker": bool(runtime.get("online"))},
        "calls_today": len(calls),
        "success_rate": round(100 * sum(c.status == "succeeded" for c in calls) / len(calls), 1)
        if calls
        else 100,
        "average_ms": round(sum(c.duration_ms for c in calls) / len(calls)) if calls else 0,
        "pending_tasks": len(task_rows),
        "models_total": len(model_rows),
        "models_loaded": sum(m.state == "ready" for m in model_rows),
        "runtime": runtime,
    }
