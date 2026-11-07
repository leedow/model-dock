"""Explicit administrator-triggered downloads; only the leased Runtime Worker writes weights."""

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select, update

from modeldock.adapters import config_example
from modeldock.entities import ModelDownload
from modeldock.errors import DockError


@dataclass(frozen=True)
class SpeechPreset:
    name: str
    repo: str
    adapter: str
    files: tuple[str, ...]

    @property
    def config(self) -> dict[str, Any]:
        return config_example(self.adapter)


CATALOG: dict[str, SpeechPreset] = {
    "sensevoice": SpeechPreset(
        "SenseVoiceSmall",
        "FunAudioLLM/SenseVoiceSmall",
        "local.sensevoice.asr",
        ("model.pt", "config.yaml", "configuration.json", "am.mvn", "chn_jpn_yue_eng_ko_spectok.bpe.model"),
    ),
    "paraformer": SpeechPreset(
        "Paraformer Chinese streaming",
        "funasr/paraformer-zh-streaming",
        "local.paraformer.asr",
        ("model.pt", "config.yaml", "configuration.json", "am.mvn", "tokens.json", "seg_dict"),
    ),
    "faster-whisper": SpeechPreset(
        "Faster-Whisper base",
        "Systran/faster-whisper-base",
        "local.faster_whisper.asr",
        ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"),
    ),
    "kokoro": SpeechPreset(
        "Kokoro Chinese 1.1",
        "hexgrad/Kokoro-82M-v1.1-zh",
        "local.kokoro.tts",
        ("kokoro-v1_1-zh.pth", "config.json", "voices/zf_001.pt"),
    ),
}


def public_download(row: ModelDownload) -> dict[str, Any]:
    return {
        key: getattr(row, key)
        for key in (
            "id",
            "preset",
            "status",
            "downloaded_bytes",
            "total_bytes",
            "current_file",
            "revision",
            "error_code",
            "cancel_requested",
            "created_at",
            "finished_at",
        )
    }


def hub_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise DockError("Configure an HTTPS model hub origin", "download_hub_invalid", 503)
    return value.rstrip("/")


class DownloadManager:
    def __init__(self, sessions: Any, settings: Any, worker_id: str, stop: asyncio.Event):
        self.sessions, self.settings, self.worker_id, self.stop = sessions, settings, worker_id, stop

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=False)

    async def change(self, download_id: str, **values: Any) -> None:
        async with self.sessions() as session:
            await session.execute(
                update(ModelDownload)
                .where(
                    ModelDownload.id == download_id,
                    ModelDownload.worker_id == self.worker_id,
                    ModelDownload.status == "running",
                )
                .values(**values)
            )
            await session.commit()

    async def recover(self) -> None:
        # The Worker acquired the unique deployment lease before starting this loop.
        async with self.sessions() as session:
            abandoned = list(
                await session.scalars(select(ModelDownload.id).where(ModelDownload.status == "running"))
            )
            await session.execute(
                update(ModelDownload)
                .where(ModelDownload.status == "running")
                .values(
                    status="failed",
                    error_code="worker_lost",
                    finished_at=time.time(),
                )
            )
            await session.commit()
        root = self.settings.models_dir.resolve()
        for download_id in abandoned:
            staging = (root / ".downloads" / download_id).resolve()
            if staging.is_relative_to(root):
                await asyncio.to_thread(shutil.rmtree, staging, True)

    async def claim(self) -> str | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ModelDownload)
                .where(ModelDownload.status == "queued")
                .order_by(ModelDownload.created_at)
                .limit(1)
            )
            if row is None:
                return None
            claimed = await session.execute(
                update(ModelDownload)
                .where(ModelDownload.id == row.id, ModelDownload.status == "queued")
                .values(status="running", worker_id=self.worker_id)
            )
            await session.commit()
            return row.id if claimed.rowcount == 1 else None

    async def serve(self) -> None:
        recovered = False
        while not self.stop.is_set():
            try:
                if not recovered:
                    await self.recover()
                    recovered = True
                download_id = await self.claim()
                if download_id:
                    await self.execute(download_id)
                else:
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("modeldock.downloads").warning("model_download_queue_retry")
                await asyncio.sleep(1)

    async def execute(self, download_id: str) -> None:
        transfer = asyncio.create_task(self.transfer(download_id))
        try:
            async with asyncio.timeout(self.settings.model_download_timeout_seconds):
                while not transfer.done():
                    await asyncio.wait({transfer}, timeout=0.25)
                    if transfer.done():
                        break
                    async with self.sessions() as session:
                        row = await session.get(ModelDownload, download_id)
                    if row is None or row.cancel_requested or self.stop.is_set():
                        transfer.cancel()
                        await asyncio.gather(transfer, return_exceptions=True)
                        await self.change(download_id, status="cancelled", finished_at=time.time())
                        return
                await transfer
                await self.change(download_id, status="succeeded", current_file=None, finished_at=time.time())
        except asyncio.CancelledError:
            transfer.cancel()
            await asyncio.gather(transfer, return_exceptions=True)
            await self.change(
                download_id, status="failed", error_code="worker_stopped", finished_at=time.time()
            )
            raise
        except Exception as exc:
            transfer.cancel()
            await asyncio.gather(transfer, return_exceptions=True)
            code = (
                exc.error["code"]
                if isinstance(exc, DockError)
                else "download_timeout"
                if isinstance(exc, TimeoutError)
                else "download_failed"
            )
            await self.change(download_id, status="failed", error_code=code, finished_at=time.time())

    async def transfer(self, download_id: str) -> None:
        async with self.sessions() as session:
            row = await session.get(ModelDownload, download_id)
        preset = CATALOG[row.preset]
        root = self.settings.models_dir.resolve()
        target = (root / preset.config["model_path"]).resolve()
        if not target.is_relative_to(root):
            raise DockError("Invalid model directory", "download_path_invalid", 503)
        if target.exists():
            raise DockError(
                "Model directory already exists; existing weights are preserved", "model_path_exists", 409
            )
        staging = (root / ".downloads" / download_id).resolve()
        if not staging.is_relative_to(root):
            raise DockError("Invalid staging directory", "download_path_invalid", 503)
        await asyncio.to_thread(staging.mkdir, parents=True, exist_ok=True)
        limit = self.settings.model_download_max_gb * 1024**3
        try:
            base = hub_url(self.settings.model_hub_url)
            async with self.client() as client:
                response = await client.get(
                    f"{base}/api/models/{preset.repo}/revision/main", params={"blobs": "true"}
                )
                response.raise_for_status()
                manifest = response.json()
                revision = manifest["sha"]
                if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
                    raise ValueError("Invalid revision")
                entries = {item["rfilename"]: item for item in manifest["siblings"]}
                if not set(preset.files) <= entries.keys():
                    raise DockError(
                        "Model repository is missing required files", "download_manifest_invalid", 502
                    )
                # Retain upstream license/readme files without executing any repository code.
                names = [
                    *preset.files,
                    *(name for name in ("LICENSE", "LICENSE.txt", "README.md") if name in entries),
                ]
                sizes = [int(entries[name].get("size", 0)) for name in names]
                if any(size <= 0 for size in sizes) or sum(sizes) > limit:
                    raise DockError(
                        "Model snapshot size is missing or exceeds configured limit",
                        "download_size_limit",
                        413,
                    )
                total = sum(sizes)
                free = (await asyncio.to_thread(shutil.disk_usage, root)).free
                if free < total + 100 * 1024**2:
                    raise DockError("Insufficient disk space for model", "download_disk_full", 503)
                await self.change(download_id, revision=revision, total_bytes=total, downloaded_bytes=0)
                downloaded = 0
                last_progress = 0.0
                hashes = {}
                for name, expected_size in zip(names, sizes, strict=True):
                    destination = staging / name
                    await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
                    await self.change(download_id, current_file=name)
                    url = f"{base}/{preset.repo}/resolve/{revision}/{quote(name, safe='/')}"
                    # HF redirects LFS to signed CDN URLs; never follow to arbitrary origins.
                    for _ in range(8):
                        async with client.stream("GET", url) as stream:
                            if stream.is_redirect:
                                redirect = stream.headers.get("location", "")
                                from urllib.parse import urljoin

                                url = urljoin(url, redirect)
                                parsed = urlsplit(url)
                                host = parsed.hostname or ""
                                trusted = (
                                    host == urlsplit(base).hostname
                                    or host == "huggingface.co"
                                    or host.endswith((".huggingface.co", ".hf.co"))
                                )
                                if (
                                    parsed.scheme != "https"
                                    or not trusted
                                    or parsed.username
                                    or parsed.password
                                ):
                                    raise DockError(
                                        "Untrusted model download redirect", "download_redirect_invalid", 502
                                    )
                                continue
                            stream.raise_for_status()
                            digest = hashlib.sha256()
                            git_digest = hashlib.sha1(f"blob {expected_size}\0".encode())
                            size = 0
                            with destination.open("wb") as output:
                                async for chunk in stream.aiter_bytes(1024 * 1024):
                                    size += len(chunk)
                                    downloaded += len(chunk)
                                    if size > expected_size or downloaded > limit:
                                        raise DockError(
                                            "Model download exceeds declared size", "download_size_limit", 413
                                        )
                                    await asyncio.to_thread(output.write, chunk)
                                    digest.update(chunk)
                                    git_digest.update(chunk)
                                    if time.monotonic() - last_progress >= 0.5:
                                        await self.change(download_id, downloaded_bytes=downloaded)
                                        last_progress = time.monotonic()
                            lfs = entries[name].get("lfs") or {}
                            expected_hash = lfs.get("sha256")
                            blob_hash = entries[name].get("blobId")
                            valid_hash = (
                                digest.hexdigest() == expected_hash
                                if expected_hash
                                else git_digest.hexdigest() == blob_hash
                            )
                            if size != expected_size or not valid_hash:
                                raise DockError(
                                    "Model file checksum mismatch", "download_checksum_failed", 502
                                )
                            hashes[name] = digest.hexdigest()
                            break
                    else:
                        raise DockError("Too many model download redirects", "download_redirect_invalid", 502)
                await asyncio.to_thread(
                    (staging / ".modeldock-manifest.json").write_text,
                    json.dumps({"repo": preset.repo, "revision": revision, "sha256": hashes}, indent=2),
                    encoding="utf-8",
                )
                await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                # A partial directory is never visible as an installed model.
                if target.exists():
                    raise DockError("Model directory appeared during download", "model_path_exists", 409)
                publication = asyncio.create_task(self.publish(download_id, staging, target, downloaded))
                try:
                    await asyncio.shield(publication)
                except asyncio.CancelledError:
                    # Once publishing begins, finish the atomic directory move and DB
                    # update before allowing cancellation/shutdown to report a result.
                    await publication
        finally:
            await asyncio.to_thread(shutil.rmtree, staging, True)

    async def publish(self, download_id: str, staging: Path, target: Path, downloaded: int) -> None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ModelDownload).where(ModelDownload.id == download_id).with_for_update()
            )
            if not row or row.status != "running" or row.worker_id != self.worker_id:
                raise DockError("Download worker no longer owns this task", "worker_lost", 503)
            if row.cancel_requested or self.stop.is_set():
                row.status, row.finished_at = "cancelled", time.time()
                await session.commit()
                return
            # Hold the row lock across publication so a replacement Worker cannot
            # fence this task between the ownership check and atomic rename.
            staging.rename(target)
            row.downloaded_bytes, row.status = downloaded, "succeeded"
            row.current_file, row.finished_at = None, time.time()
            try:
                await session.commit()
            except BaseException:
                # No installed path is advertised if the completion transaction fails.
                target.rename(staging)
                raise
