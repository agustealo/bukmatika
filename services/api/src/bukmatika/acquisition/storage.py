import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from uuid import UUID


@dataclass(frozen=True, slots=True)
class StoredFile:
    storage_key: str
    path: Path


class LocalObjectStore:
    """Content-addressed local storage with atomic finalization."""

    _formats: ClassVar[frozenset[str]] = frozenset({"DOC", "DOCX", "EPUB", "HTML", "PDF", "TXT"})

    def __init__(self, root: Path) -> None:
        self._root = root

    async def create_temp_path(self, acquisition_id: UUID) -> Path:
        incoming = self._root / "incoming"
        await asyncio.to_thread(incoming.mkdir, parents=True, exist_ok=True)
        path = incoming / f"{acquisition_id}.part"
        await self.discard(path)
        return path

    async def commit(self, temp_path: Path, *, sha256: str, format_name: str) -> StoredFile:
        if format_name.upper() not in self._formats:
            raise ValueError(f"Unsupported stored format: {format_name}")
        relative = Path("objects") / sha256[:2] / sha256[2:4] / sha256
        destination = self._root / relative
        await asyncio.to_thread(destination.parent.mkdir, parents=True, exist_ok=True)
        if await asyncio.to_thread(destination.exists):
            await self.discard(temp_path)
        else:
            await asyncio.to_thread(os.replace, temp_path, destination)
        return StoredFile(storage_key=relative.as_posix(), path=destination)

    async def quarantine(self, temp_path: Path, acquisition_id: UUID) -> str:
        quarantine_dir = self._root / "quarantine"
        await asyncio.to_thread(quarantine_dir.mkdir, parents=True, exist_ok=True)
        relative = Path("quarantine") / f"{acquisition_id}.bin"
        destination = self._root / relative
        await self.discard(destination)
        await asyncio.to_thread(os.replace, temp_path, destination)
        return relative.as_posix()

    async def discard(self, path: Path) -> None:
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return
