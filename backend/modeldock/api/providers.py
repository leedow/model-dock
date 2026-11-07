"""Administrative connection endpoints. Explicit public projection excludes ciphertext."""

import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from modeldock.entities import Model, Outbox, ProviderConnection, Task
from modeldock.errors import DockError
from modeldock.schemas import ProviderConnectionList, ProviderConnectionOutput, ProviderPresetList
from modeldock.security import Principal, admin
from modeldock.services.providers import PRESETS, ConnectionInput, encrypt_key, lock_connections

router = APIRouter(prefix="/api/v1", tags=["Provider connections"])


def public_connection(row: ProviderConnection, models: list[Model]) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "base_url": row.base_url,
        "connect_timeout": row.connect_timeout,
        "read_timeout": row.read_timeout,
        "has_api_key": bool(row.encrypted_api_key),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "models": [{"id": m.id, "name": m.name, "state": m.state} for m in models],
    }


@router.get("/provider-presets", response_model=ProviderPresetList)
async def presets(principal: Principal = Depends(admin)):
    return {"data": PRESETS}


@router.get("/provider-connections", response_model=ProviderConnectionList)
async def connections(request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        rows = list(await session.scalars(select(ProviderConnection).order_by(ProviderConnection.created_at)))
        models = list(await session.scalars(select(Model).where(Model.provider_connection_id.is_not(None))))
        return {
            "data": [
                public_connection(c, [m for m in models if m.provider_connection_id == c.id]) for c in rows
            ]
        }


@router.post("/provider-connections", status_code=201, response_model=ProviderConnectionOutput)
async def create_connection(body: ConnectionInput, request: Request, principal: Principal = Depends(admin)):
    key = body.api_key.get_secret_value() if body.api_key else ""
    if not key.strip():
        raise DockError("API key is required", "provider_credentials_missing", 422)
    encrypted = encrypt_key(request.app.state.settings, key)
    async with request.app.state.sessions() as session:
        row = ProviderConnection(**body.model_dump(exclude={"api_key"}), encrypted_api_key=encrypted)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            raise DockError("Connection name already exists", "duplicate_connection", 409) from None
        return public_connection(row, [])


@router.put("/provider-connections/{connection_id}", response_model=ProviderConnectionOutput)
async def update_connection(
    connection_id: str, body: ConnectionInput, request: Request, principal: Principal = Depends(admin)
):
    async with request.app.state.sessions() as session:
        row = (await lock_connections(session, [connection_id]))[connection_id]
        models = list(
            await session.scalars(
                select(Model)
                .where(Model.provider_connection_id == connection_id)
                .order_by(Model.id)
                .with_for_update()
            )
        )
        ids = [m.id for m in models]
        busy = await session.scalar(
            select(Task.id).where(Task.model_id.in_(ids), Task.status.in_(["queued", "running"])).limit(1)
        )
        pending = await session.scalar(
            select(Outbox.id)
            .where(Outbox.target_id.in_(ids), Outbox.kind == "load", Outbox.delivered.is_(False))
            .limit(1)
        )
        if any(m.state != "unloaded" for m in models) or busy or pending:
            raise DockError(
                "Unload all connected models and finish pending tasks before editing",
                "provider_connection_busy",
                409,
            )
        if models and body.provider != row.provider:
            raise DockError(
                "Cannot change provider while models reference this connection",
                "provider_connection_in_use",
                409,
            )
        for name, value in body.model_dump(exclude={"api_key"}).items():
            setattr(row, name, value)
        key = body.api_key.get_secret_value() if body.api_key else ""
        if key:
            if not key.strip():
                raise DockError("API key cannot be whitespace", "provider_credentials_missing", 422)
            row.encrypted_api_key = encrypt_key(request.app.state.settings, key)
        row.updated_at = time.time()
        try:
            await session.commit()
        except IntegrityError:
            raise DockError("Connection name already exists", "duplicate_connection", 409) from None
        return public_connection(row, models)


@router.delete("/provider-connections/{connection_id}", status_code=204)
async def delete_connection(connection_id: str, request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        row = (await lock_connections(session, [connection_id]))[connection_id]
        if await session.scalar(
            select(Model.id).where(Model.provider_connection_id == connection_id).limit(1)
        ):
            raise DockError("Connection is referenced by models", "provider_connection_in_use", 409)
        await session.delete(row)
        await session.commit()
