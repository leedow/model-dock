import asyncio
import contextlib
import io
import time
import wave
from pathlib import Path, PurePath
from typing import Any

from sqlalchemy import select

from modeldock.entities import StoredFile, Task, uid
from modeldock.errors import DockError


def public_file(file: StoredFile) -> dict[str, Any]:
    return {
        "id": file.id,
        "filename": file.filename,
        "media_type": file.media_type,
        "size": file.size,
        "created_at": file.created_at,
        "expires_at": file.expires_at,
        "task_id": file.task_id,
        "url": f"/v1/files/{file.id}/content",
    }


async def save_file(
    state: Any,
    user_id: str,
    filename: str,
    media_type: str,
    content: bytes | Path,
    task_id: str | None = None,
) -> StoredFile:
    size = content.stat().st_size if isinstance(content, Path) else len(content)
    if not size or size > state.settings.upload_max_mb * 1024 * 1024:
        raise DockError("File is empty or exceeds upload size limit", "file_size", 413)
    filename = PurePath(filename.replace("\\", "/")).name[:180] or "file"
    file = StoredFile(
        id=uid(),
        user_id=user_id,
        filename=filename,
        media_type=media_type,
        size=size,
        task_id=task_id,
        expires_at=time.time() + state.settings.file_retention_days * 86400,
    )
    operation = asyncio.create_task(
        state.storage.put_path(file.id, content)
        if isinstance(content, Path)
        else state.storage.put(file.id, content)
    )
    try:
        await asyncio.shield(operation)
        async with state.sessions() as session:
            session.add(file)
            await session.commit()
    except BaseException:
        with contextlib.suppress(Exception):
            await operation
        await state.storage.delete(file.id)
        raise
    return file


async def owned_file(state: Any, user_id: str, file_id: str) -> StoredFile:
    async with state.sessions() as session:
        file = await session.get(StoredFile, file_id)
        if file is None or file.user_id != user_id or file.expires_at <= time.time():
            raise DockError("File not found or expired", "not_found", 404)
        return file


async def file_is_busy(state: Any, file_id: str) -> bool:
    async with state.sessions() as session:
        tasks = await session.scalars(select(Task).where(Task.status.in_(["queued", "running"])))
        return any(
            file_id in input_file_ids(task.payload)
            or any(f.get("id") == file_id for f in (task.result or {}).get("files", []))
            for task in tasks
        )


def validate_audio(content: bytes) -> None:
    # The v1 mock contract accepts PCM WAV only; actual codec support belongs to the ASR adapter.
    try:
        with wave.open(io.BytesIO(content), "rb") as audio:
            if audio.getnframes() <= 0 or audio.getnchannels() not in {1, 2}:
                raise ValueError("Empty or unsupported channels")
            if not audio.readframes(min(audio.getnframes(), 100)):
                raise ValueError("Empty PCM")
    except (wave.Error, EOFError, ValueError):
        raise DockError("Mock ASR accepts a valid PCM WAV file", "invalid_audio", param="file") from None


def input_file_ids(payload: dict[str, Any]) -> list[str]:
    return sorted({str(value) for key, value in payload.items() if key.endswith("file_id") and value})


async def validate_media(path: Path, filename: str) -> str:
    """Inspect decoded media rather than trusting a supplied MIME or extension."""
    import asyncio
    import json

    from PIL import Image

    suffix = Path(filename).suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:

        def inspect_image() -> str:
            with Image.open(path) as image:
                fmt = image.format
                image.verify()
            return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[str(fmt)]

        try:
            return await asyncio.to_thread(inspect_image)
        except (OSError, ValueError, KeyError, SyntaxError, Image.DecompressionBombError):
            raise DockError("Invalid image", "invalid_media") from None
    if suffix in {".txt", ".ass", ".srt"}:
        try:
            text = (await asyncio.to_thread(path.read_bytes)).decode("utf-8-sig")
            if not text.strip() or "\x00" in text:
                raise ValueError
            if suffix == ".ass" and ("[Script Info]" not in text or "[Events]" not in text):
                raise ValueError
            return "text/x-ssa" if suffix == ".ass" else "text/plain"
        except (UnicodeError, ValueError):
            raise DockError("Invalid UTF-8 text or subtitle", "invalid_media") from None
    if suffix not in {".wav", ".mp3", ".flac", ".mp4"}:
        raise DockError("Unsupported media format", "invalid_media")
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), 15)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        data = json.loads(output)
        streams = data.get("streams", [])
        expected = {".wav": "wav", ".mp3": "mp3", ".flac": "flac", ".mp4": "mp4"}[suffix]
        if process.returncode or expected not in data.get("format", {}).get("format_name", "").split(","):
            raise ValueError
        kind = "video" if suffix == ".mp4" else "audio"
        if not any(s.get("codec_type") == kind for s in streams):
            raise ValueError
        return {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac", ".mp4": "video/mp4"}[suffix]
    except FileNotFoundError:
        raise DockError("Media inspection requires ffprobe", "media_inspector_unavailable", 503) from None
    except (ValueError, TimeoutError):
        raise DockError("Invalid audio or video", "invalid_media") from None
