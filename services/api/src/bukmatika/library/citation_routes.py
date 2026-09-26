from typing import Annotated

from fastapi import APIRouter, Depends, Response

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.citations import CitationExportFormat, LibraryCitationExportService

router = APIRouter(prefix="/v1/library/citations", tags=["library"])
_citation_service = LibraryCitationExportService()


def citation_service() -> LibraryCitationExportService:
    return _citation_service


@router.get("/{format_name}")
async def export_library_citations(
    format_name: CitationExportFormat,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[LibraryCitationExportService, Depends(citation_service)],
) -> Response:
    exported = await service.export(
        principal_id=identity.principal_id,
        format_name=format_name,
    )
    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "Cache-Control": "private, no-store",
        },
    )
