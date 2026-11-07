import asyncio
import base64
import contextlib
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, select

from modeldock.adapters import operations
from modeldock.entities import Model, StoredFile, Task
from modeldock.errors import DockError
from modeldock.schemas import (
    ASRParams,
    ChatCompletion,
    ChatInput,
    FileList,
    FileOutput,
    ImageInput,
    ImageOutput,
    ImageTaskParams,
    ModelList,
    SpeechInput,
    SubtitleParams,
    TaskInput,
    TaskList,
    TaskOutput,
    TranscriptionOutput,
    VideoParams,
)
from modeldock.security import Principal, authenticate
from modeldock.services.files import file_is_busy, owned_file, public_file, save_file, validate_audio
from modeldock.services.tasks import TERMINAL, cancel_task, create_task, get_owned_task, public_task

router = APIRouter(prefix="/v1", tags=["Inference"])


@router.get("/models", response_model=ModelList)
async def models(request: Request, principal: Principal = Depends(authenticate)):
    async with request.app.state.sessions() as session:
        query = select(Model).where(Model.enabled.is_(True))
        if principal.key_id or principal.role != "admin":
            query = query.where(Model.id.in_(principal.model_ids))
        return {
            "object": "list",
            "data": [
                {
                    "id": m.name,
                    "object": "model",
                    "created": int(m.created_at),
                    "owned_by": "modeldock",
                    "capability": m.capability,
                    "operations": operations(m.adapter, m.config),
                }
                for m in await session.scalars(query)
            ],
        }


async def submitted(
    request: Request, principal: Principal, model: str, capability: str, payload: dict[str, Any]
) -> Task:
    task = await create_task(
        request.app.state, principal, model, capability, payload, request.state.request_id
    )
    request.state.task_id = task.id
    return task


async def synchronous(
    request: Request, principal: Principal, model: str, capability: str, payload: dict[str, Any]
) -> dict[str, Any]:
    if not (await request.app.state.runtime.snapshot()).get("online"):
        raise DockError("Runtime Worker is offline", "worker_unavailable", 503, error_type="server_error")
    task = await submitted(request, principal, model, capability, payload)
    async with request.app.state.sessions() as session:
        model_row = await session.get(Model, task.model_id)
    try:
        return await request.app.state.runtime.wait(task.id, model_row.timeout_seconds + 10)
    except (DockError, asyncio.CancelledError):
        await cancel_task(request.app.state, task.id)
        raise


def chat_chunk(task: Task, delta: dict[str, Any], finish: str | None = None) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{task.id}",
        "object": "chat.completion.chunk",
        "created": int(task.created_at),
        "model": task.model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }


@router.post(
    "/chat/completions",
    response_model=ChatCompletion,
    responses={200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}}},
)
async def chat(body: ChatInput, request: Request, principal: Principal = Depends(authenticate)):
    payload = body.model_dump(exclude_none=True)
    if not body.stream:
        result = await synchronous(request, principal, body.model, "chat", payload)
        return {
            "id": f"chatcmpl-{request.state.request_id}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result["text"]},
                    "finish_reason": result.get("finish_reason", "stop"),
                    "logprobs": None,
                }
            ],
            "usage": result["usage"],
        }
    if not (await request.app.state.runtime.snapshot()).get("online"):
        raise DockError("Runtime Worker is offline", "worker_unavailable", 503, error_type="server_error")
    task = await submitted(request, principal, body.model, "chat", payload)
    async with request.app.state.sessions() as session:
        model_row = await session.get(Model, task.model_id)
        stream_timeout = model_row.timeout_seconds + 10

    async def events():
        cursor = "0-0"
        finished = False
        deadline = time.monotonic() + stream_timeout
        try:
            yield "data: " + json.dumps(chat_chunk(task, {"role": "assistant", "content": ""})) + "\n\n"
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    return
                batches = await request.app.state.redis.xread(
                    {f"modeldock:events:{task.id}": cursor}, count=100, block=250
                )
                for _, entries in batches:
                    for event_id, entry in entries:
                        cursor = event_id
                        event = json.loads(entry["data"])
                        if "delta" in event:
                            yield (
                                "data: "
                                + json.dumps(
                                    chat_chunk(task, {"content": event["delta"]}), ensure_ascii=False
                                )
                                + "\n\n"
                            )
                async with request.app.state.sessions() as session:
                    current = await session.get(Task, task.id)
                if current and current.status in TERMINAL:
                    # A terminal DB write can race the final Redis append; drain remaining deltas once.
                    remaining = await request.app.state.redis.xrange(
                        f"modeldock:events:{task.id}", min=f"({cursor}", max="+"
                    )
                    for event_id, entry in remaining:
                        cursor = event_id
                        event = json.loads(entry["data"])
                        if "delta" in event:
                            yield (
                                "data: "
                                + json.dumps(
                                    chat_chunk(task, {"content": event["delta"]}), ensure_ascii=False
                                )
                                + "\n\n"
                            )
                    if current.status != "succeeded":
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    "error": current.error
                                    or {
                                        "message": "Cancelled",
                                        "type": "server_error",
                                        "param": None,
                                        "code": "cancelled",
                                    }
                                }
                            )
                            + "\n\n"
                        )
                        finished = True
                        return
                    yield (
                        "data: "
                        + json.dumps(
                            chat_chunk(task, {}, (current.result or {}).get("finish_reason", "stop"))
                        )
                        + "\n\n"
                    )
                    if body.stream_options and body.stream_options.include_usage:
                        yield (
                            "data: "
                            + json.dumps(
                                {
                                    **chat_chunk(task, {}),
                                    "choices": [],
                                    "usage": (current.result or {}).get("usage", {}),
                                }
                            )
                            + "\n\n"
                        )
                    yield "data: [DONE]\n\n"
                    finished = True
                    return
            yield 'data: {"error":{"message":"Stream timed out","code":"timeout","type":"server_error","param":null}}\n\n'
        except Exception:
            yield 'data: {"error":{"message":"Stream transport unavailable","code":"stream_unavailable","type":"server_error","param":null}}\n\n'
        finally:
            if not finished:
                with contextlib.suppress(Exception):
                    await asyncio.shield(cancel_task(request.app.state, task.id))

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/images/generations", response_model=ImageOutput, response_model_exclude_none=True)
async def image(body: ImageInput, request: Request, principal: Principal = Depends(authenticate)):
    result = await synchronous(request, principal, body.model, "image", body.model_dump())
    data = []
    for file in result["files"]:
        item = {"file_id": file["id"]}
        if body.response_format == "b64_json":
            item["b64_json"] = base64.b64encode(await request.app.state.storage.read(file["id"])).decode()
        else:
            item["url"] = str(request.base_url).rstrip("/") + file["url"]
        data.append(item)
    return {"created": int(time.time()), "data": data, "mock": result.get("mock", False)}


@router.post("/audio/speech", responses={200: {"content": {"audio/wav": {}, "audio/mpeg": {}}}})
async def speech(body: SpeechInput, request: Request, principal: Principal = Depends(authenticate)):
    result = await synchronous(request, principal, body.model, "tts", body.model_dump())
    file = result["files"][0]
    return Response(
        await request.app.state.storage.read(file["id"]),
        media_type=file["media_type"],
        headers={
            "X-File-ID": file["id"],
            "Content-Disposition": f'attachment; filename="{file["filename"]}"',
        },
    )


@router.post("/audio/transcriptions", response_model=TranscriptionOutput)
async def transcription(
    request: Request,
    model: str = Form(...),
    file: UploadFile = File(...),
    language: str | None = Form(None),
    response_format: str = Form("json"),
    principal: Principal = Depends(authenticate),
):
    if response_format not in {"json", "text"}:
        raise DockError(
            "Supported response formats: json, text", "unsupported_format", param="response_format"
        )
    content = await file.read(request.app.state.settings.upload_max_mb * 1024 * 1024 + 1)
    validate_audio(content)
    stored = await save_file(
        request.app.state, principal.user_id, file.filename or "audio.wav", "audio/wav", content
    )
    result = await synchronous(request, principal, model, "asr", {"file_id": stored.id, "language": language})
    return Response(result["text"], media_type="text/plain") if response_format == "text" else result


@router.post("/tasks", status_code=202, response_model=TaskOutput)
async def task_create(body: TaskInput, request: Request, principal: Principal = Depends(authenticate)):
    try:
        if body.capability == "image":
            parameters = ImageTaskParams.model_validate({**body.parameters, "model": body.model}).model_dump()
        elif body.capability == "tts":
            parameters = SpeechInput.model_validate({**body.parameters, "model": body.model}).model_dump()
        elif body.capability == "video":
            parameters = VideoParams.model_validate(body.parameters).model_dump()
        elif body.capability == "subtitle":
            parameters = SubtitleParams.model_validate(body.parameters).model_dump()
        else:
            parameters = ASRParams.model_validate(body.parameters).model_dump()
    except ValidationError as exc:
        raise DockError(
            str(exc.errors(include_input=False, include_url=False)), "validation_error", 422
        ) from None
    task = await submitted(request, principal, body.model, body.capability, parameters)
    return public_task(task)


@router.get("/tasks", response_model=TaskList)
async def tasks(
    request: Request,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
    principal: Principal = Depends(authenticate),
):
    if offset < 0 or not 1 <= limit <= 200:
        raise DockError("Invalid pagination", "invalid_pagination")
    query = select(Task).where(
        Task.user_id == principal.user_id, Task.expires_at > time.time(), Task.capability != "chat"
    )
    if status:
        query = query.where(Task.status == status)
    async with request.app.state.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(query.order_by(Task.created_at.desc()).offset(offset).limit(limit))
        return {"data": [public_task(t) for t in rows], "total": total}


@router.get("/tasks/{task_id}", response_model=TaskOutput)
async def task_get(task_id: str, request: Request, principal: Principal = Depends(authenticate)):
    return public_task(await get_owned_task(request.app.state, principal, task_id))


@router.post("/tasks/{task_id}/cancel", status_code=202, response_model=TaskOutput)
async def task_cancel(task_id: str, request: Request, principal: Principal = Depends(authenticate)):
    await get_owned_task(request.app.state, principal, task_id)
    await cancel_task(request.app.state, task_id)
    return public_task(await get_owned_task(request.app.state, principal, task_id))


@router.post("/tasks/{task_id}/retry", status_code=202, response_model=TaskOutput)
async def task_retry(task_id: str, request: Request, principal: Principal = Depends(authenticate)):
    original = await get_owned_task(request.app.state, principal, task_id)
    if original.status not in {"failed", "cancelled"}:
        raise DockError("Only failed or cancelled tasks can be retried", "invalid_task_state", 409)
    task = await submitted(request, principal, original.model_name, original.capability, original.payload)
    return public_task(task)


@router.get("/files", response_model=FileList)
async def files(
    request: Request,
    media_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
    principal: Principal = Depends(authenticate),
):
    if offset < 0 or not 1 <= limit <= 200:
        raise DockError("Invalid pagination", "invalid_pagination")
    query = select(StoredFile).where(
        StoredFile.user_id == principal.user_id, StoredFile.expires_at > time.time()
    )
    if media_type:
        query = query.where(StoredFile.media_type == media_type)
    async with request.app.state.sessions() as session:
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await session.scalars(query.order_by(StoredFile.created_at.desc()).offset(offset).limit(limit))
        return {"data": [public_file(f) for f in rows], "total": total}


@router.post("/files", status_code=201, response_model=FileOutput)
async def file_upload(
    request: Request, file: UploadFile = File(...), principal: Principal = Depends(authenticate)
):
    content = await file.read(request.app.state.settings.upload_max_mb * 1024 * 1024 + 1)
    import os
    import tempfile
    from pathlib import Path

    from modeldock.services.files import validate_media

    if not content or len(content) > request.app.state.settings.upload_max_mb * 1024 * 1024:
        raise DockError("File is empty or exceeds upload size limit", "file_size", 413)
    fd, name = tempfile.mkstemp(prefix="modeldock-upload-")
    os.close(fd)
    path = Path(name)
    try:
        path.write_bytes(content)
        media_type = await validate_media(path, file.filename or "file")
        stored = await save_file(
            request.app.state, principal.user_id, file.filename or "file", media_type, path
        )
    finally:
        path.unlink(missing_ok=True)
    return public_file(stored)


@router.get("/files/{file_id}", response_model=FileOutput)
async def file_get(file_id: str, request: Request, principal: Principal = Depends(authenticate)):
    return public_file(await owned_file(request.app.state, principal.user_id, file_id))


@router.get("/files/{file_id}/content")
async def file_content(file_id: str, request: Request, principal: Principal = Depends(authenticate)):
    file = await owned_file(request.app.state, principal.user_id, file_id)
    return FileResponse(
        request.app.state.storage.path(file.id),
        media_type=file.media_type,
        headers={
            "Content-Disposition": f'inline; filename="{file.id}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/files/{file_id}", status_code=204)
async def file_delete(file_id: str, request: Request, principal: Principal = Depends(authenticate)):
    async with request.app.state.sessions() as session:
        file = await session.get(StoredFile, file_id, with_for_update=True)
        if not file or file.user_id != principal.user_id or file.expires_at <= time.time():
            raise DockError("File not found or expired", "not_found", 404)
        if await file_is_busy(request.app.state, file_id):
            raise DockError("File is referenced by a pending task", "file_busy", 409)
        await session.delete(file)
        await session.commit()
    await request.app.state.storage.delete(file_id)
