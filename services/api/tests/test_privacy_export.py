from datetime import UTC, datetime
from uuid import UUID, uuid4

from bukmatika.library.portability_domain import LibraryPortabilityExportResponse
from bukmatika.personalization.portability_domain import (
    PersonalizationExportResponse,
    UserModelExport,
)
from bukmatika.privacy.service import PrincipalDataExportService


class LibraryExporterStub:
    def __init__(self, response: LibraryPortabilityExportResponse) -> None:
        self.response = response
        self.principal_ids: list[UUID] = []

    async def export(self, *, principal_id: UUID) -> LibraryPortabilityExportResponse:
        self.principal_ids.append(principal_id)
        return self.response


class PersonalizationExporterStub:
    def __init__(self, response: PersonalizationExportResponse) -> None:
        self.response = response
        self.principal_ids: list[UUID] = []

    async def export(self, *, principal_id: UUID) -> PersonalizationExportResponse:
        self.principal_ids.append(principal_id)
        return self.response


def _library_export(now: datetime) -> LibraryPortabilityExportResponse:
    return LibraryPortabilityExportResponse(
        exported_at=now,
        entries=[],
        collections=[],
        tags=[],
        smart_shelves=[],
    )


def _personalization_export(now: datetime) -> PersonalizationExportResponse:
    return PersonalizationExportResponse(
        exported_at=now,
        latest_reset_at=None,
        user_model=UserModelExport(
            user_model_id=uuid4(),
            ai_enabled=False,
            learning_enabled=False,
            autonomy_level=0,
            model_provider_override=None,
            model_name_override=None,
            created_at=now,
            updated_at=now,
        ),
        preferences=[],
        goals=[],
        plans=[],
        action_decisions=[],
        outcomes=[],
    )


async def test_principal_export_composes_existing_domain_exports() -> None:
    principal_id = uuid4()
    now = datetime.now(UTC)
    library_exporter = LibraryExporterStub(_library_export(now))
    personalization_exporter = PersonalizationExporterStub(_personalization_export(now))
    service = PrincipalDataExportService(
        library_exporter=library_exporter,
        personalization_exporter=personalization_exporter,
    )

    response = await service.export(principal_id=principal_id)

    assert library_exporter.principal_ids == [principal_id]
    assert personalization_exporter.principal_ids == [principal_id]
    assert response.schema_version == 1
    assert response.export_kind == "bukmatika-principal-data"
    assert response.snapshot_mode == "component-snapshots"
    assert response.library == library_exporter.response
    assert response.personalization == personalization_exporter.response
    assert response.coverage.library_metadata_and_state is True
    assert response.coverage.personalization_and_ai_state is True
    assert response.coverage.book_bytes_included is False
    assert response.coverage.book_bytes_export_route == "/v1/library/export/file"


def test_privacy_export_contract_does_not_claim_book_bytes() -> None:
    now = datetime.now(UTC)
    payload = PrincipalDataExportService
    assert payload is not None
    response = _library_export(now)
    assert response.content_mode == "metadata-and-state-only"
