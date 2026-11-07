import time
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update

from modeldock.entities import Model, ModelDownload, User
from modeldock.errors import DockError
from modeldock.security import Principal, admin
from modeldock.services.model_downloads import CATALOG, public_download

router = APIRouter(prefix="/api/v1/model-downloads", tags=["Model downloads"])


class DownloadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preset: Literal["sensevoice", "paraformer", "faster-whisper", "kokoro"]


@router.get("/catalog")
async def catalog(principal: Principal = Depends(admin)):
    return {
        "data": [
            {
                "id": key,
                "name": preset.name,
                "repo": preset.repo,
                "adapter": preset.adapter,
                "capability": "tts" if key == "kokoro" else "asr",
                "config": preset.config,
            }
            for key, preset in CATALOG.items()
        ]
    }


@router.get("")
async def downloads(request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        rows = await session.scalars(
            select(ModelDownload)
            .where(ModelDownload.user_id == principal.user_id)
            .order_by(ModelDownload.created_at.desc())
            .limit(100)
        )
        return {"data": [public_download(row) for row in rows]}


@router.post("", status_code=202)
async def create(body: DownloadInput, request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        # Serialize catalog submissions across admins without accepting arbitrary URLs/paths.
        await session.execute(select(User.id).where(User.role == "admin").order_by(User.id).with_for_update())
        active = await session.scalar(
            select(ModelDownload.id).where(
                ModelDownload.preset == body.preset, ModelDownload.status.in_(["queued", "running"])
            )
        )
        if active:
            raise DockError("This model already has a pending download", "download_busy", 409)
        row = ModelDownload(user_id=principal.user_id, preset=body.preset)
        session.add(row)
        await session.commit()
        return public_download(row)


@router.post("/{download_id}/cancel", status_code=202)
async def cancel(download_id: str, request: Request, principal: Principal = Depends(admin)):
    async with request.app.state.sessions() as session:
        row = await session.scalar(
            select(ModelDownload)
            .where(ModelDownload.id == download_id, ModelDownload.user_id == principal.user_id)
            .with_for_update()
        )
        if row is None:
            raise DockError("Download not found", "not_found", 404)
        if row.status == "queued":
            row.status, row.finished_at, row.cancel_requested = "cancelled", time.time(), True
        elif row.status == "running":
            await session.execute(
                update(ModelDownload)
                .where(ModelDownload.id == download_id, ModelDownload.status == "running")
                .values(cancel_requested=True)
            )
        await session.commit()
        await session.refresh(row)
        return public_download(row)


@router.post("/{download_id}/register", status_code=201)
async def register(download_id: str, request: Request, principal: Principal = Depends(admin)):
    from modeldock.api.management import model_public

    async with request.app.state.sessions() as session:
        row = await session.scalar(
            select(ModelDownload)
            .where(ModelDownload.id == download_id, ModelDownload.user_id == principal.user_id)
            .with_for_update()
        )
        if not row:
            raise DockError("Download not found", "not_found", 404)
        if row.status != "succeeded":
            raise DockError("Wait for the model download to succeed", "download_not_ready", 409)
        preset = CATALOG[row.preset]
        name = "local-" + row.preset
        if await session.scalar(select(Model.id).where(Model.name == name)):
            raise DockError("A model with this name already exists", "model_exists", 409)
        model = Model(
            name=name,
            capability="tts" if row.preset == "kokoro" else "asr",
            adapter=preset.adapter,
            config=preset.config,
            device="cpu",
            memory_mb=0,
            enabled=False,
            concurrency=1,
            timeout_seconds=300,
        )
        session.add(model)
        await session.commit()
        return model_public(model)
