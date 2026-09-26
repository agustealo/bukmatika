from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from bukmatika.library.portability import LibraryPortabilityService
from bukmatika.library.portability_domain import LibraryPortabilityExportResponse
from bukmatika.personalization.portability import PersonalizationPortabilityService
from bukmatika.personalization.portability_domain import PersonalizationExportResponse
from bukmatika.privacy.domain import PrincipalDataExportCoverage, PrincipalDataExportResponse


class LibraryDataExporter(Protocol):
    async def export(self, *, principal_id: UUID) -> LibraryPortabilityExportResponse: ...


class PersonalizationDataExporter(Protocol):
    async def export(self, *, principal_id: UUID) -> PersonalizationExportResponse: ...


class PrincipalDataExportService:
    """Compose canonical principal-owned exports without becoming a third data authority."""

    def __init__(
        self,
        *,
        library_exporter: LibraryDataExporter | None = None,
        personalization_exporter: PersonalizationDataExporter | None = None,
    ) -> None:
        self._library_exporter = library_exporter or LibraryPortabilityService()
        self._personalization_exporter = (
            personalization_exporter or PersonalizationPortabilityService()
        )

    async def export(self, *, principal_id: UUID) -> PrincipalDataExportResponse:
        library = await self._library_exporter.export(principal_id=principal_id)
        personalization = await self._personalization_exporter.export(
            principal_id=principal_id
        )
        return PrincipalDataExportResponse(
            exported_at=datetime.now(UTC),
            coverage=PrincipalDataExportCoverage(),
            library=library,
            personalization=personalization,
        )


__all__ = ["PrincipalDataExportService"]
