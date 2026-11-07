import asyncio
import re
from pathlib import Path
from typing import Protocol


class FileStorage(Protocol):
    async def put(self, file_id: str, content: bytes) -> None: ...
    async def put_path(self, file_id: str, source: Path) -> None: ...
    async def read(self, file_id: str) -> bytes: ...
    async def delete(self, file_id: str) -> None: ...


class LocalFileStorage:
    def __init__(self, root: Path):
        self.root = root / "files"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, file_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9-]{36}", file_id):
            raise ValueError("Invalid storage identifier")
        return self.root / file_id

    async def put(self, file_id: str, content: bytes) -> None:
        target = self.path(file_id)
        temporary = target.with_suffix(".tmp")
        await asyncio.to_thread(temporary.write_bytes, content)
        await asyncio.to_thread(temporary.replace, target)

    async def put_path(self, file_id: str, source: Path) -> None:
        import shutil

        target = self.path(file_id)
        temporary = target.with_suffix(".tmp")
        await asyncio.to_thread(shutil.copyfile, source, temporary)
        await asyncio.to_thread(temporary.replace, target)

    async def read(self, file_id: str) -> bytes:
        return await asyncio.to_thread(self.path(file_id).read_bytes)

    async def delete(self, file_id: str) -> None:
        await asyncio.to_thread(self.path(file_id).unlink, missing_ok=True)
        await asyncio.to_thread(self.path(file_id).with_suffix(".tmp").unlink, missing_ok=True)
