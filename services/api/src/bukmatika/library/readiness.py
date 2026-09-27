from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import Document
from bukmatika.persistence.models import Asset, Edition, LibraryEntry

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class LibraryReadinessResponse(BaseModel):
    entry_count: int = Field(ge=0)
    readable_entry_count: int = Field(ge=0)


class LibraryReadinessService:
    """Lightweight principal-scoped projection for consumer readiness decisions."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._session_scope = session_scope_factory

    async def readiness(self, *, principal_id: UUID) -> LibraryReadinessResponse:
        async with self._session_scope() as database_session:
            readable_document_exists = (
                exists(
                    select(Document.id)
                    .join(Asset, Asset.id == Document.asset_id)
                    .join(Edition, Edition.id == Asset.edition_id)
                    .where(
                        Edition.work_id == LibraryEntry.work_id,
                        or_(
                            LibraryEntry.edition_id.is_(None),
                            Edition.id == LibraryEntry.edition_id,
                        ),
                    )
                )
                .correlate(LibraryEntry)
            )
            entry_count, readable_entry_count = (
                await database_session.execute(
                    select(
                        func.count(LibraryEntry.id),
                        func.count(LibraryEntry.id).filter(readable_document_exists),
                    ).where(LibraryEntry.principal_id == principal_id)
                )
            ).one()
            return LibraryReadinessResponse(
                entry_count=int(entry_count),
                readable_entry_count=int(readable_entry_count),
            )


__all__ = ["LibraryReadinessResponse", "LibraryReadinessService"]
