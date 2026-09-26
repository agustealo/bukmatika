from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from bukmatika.library.portability_domain import LibraryPortabilityExportResponse
from bukmatika.personalization.portability_domain import PersonalizationExportResponse


class PrincipalDataExportCoverage(BaseModel):
    library_metadata_and_state: Literal[True] = True
    personalization_and_ai_state: Literal[True] = True
    book_bytes_included: Literal[False] = False
    book_bytes_export_route: Literal["/v1/library/export/file"] = "/v1/library/export/file"


class PrincipalDataExportResponse(BaseModel):
    schema_version: Literal[1] = 1
    export_kind: Literal["bukmatika-principal-data"] = "bukmatika-principal-data"
    snapshot_mode: Literal["component-snapshots"] = "component-snapshots"
    exported_at: datetime
    coverage: PrincipalDataExportCoverage
    library: LibraryPortabilityExportResponse
    personalization: PersonalizationExportResponse


__all__ = [
    "PrincipalDataExportCoverage",
    "PrincipalDataExportResponse",
]
