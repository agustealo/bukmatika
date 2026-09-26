from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.citation import (
    CitationExportNotFound,
    CitationExportService,
    CitationFormat,
)
from bukmatika.persistence import session_scope

router = APIRouter(prefix="/v1/dossiers", tags=["library"])


@router.get("/works/{work_id}/editions/{edition_id}/citation")
async def export_citation(
    work_id: UUID,
    edition_id: UUID,
    citation_format: Annotated[CitationFormat, Query(alias="format")],
    _identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
) -> Response:
    async with session_scope() as database_session:
        try:
            exported = await CitationExportService(database_session).export(
                work_id=work_id,
                edition_id=edition_id,
                citation_format=citation_format,
            )
        except CitationExportNotFound as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Edition citation not found",
            ) from exc

    return Response(
        content=exported.content,
        media_type=exported.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{exported.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
