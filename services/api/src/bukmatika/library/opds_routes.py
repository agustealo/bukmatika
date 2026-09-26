from typing import Annotated

from fastapi import APIRouter, Depends, Response

from bukmatika.config import get_settings
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.library.opds import OPDS_MEDIA_TYPE, OpdsCatalogService

router = APIRouter(prefix="/v1/opds", tags=["library-opds"])
_opds_service = OpdsCatalogService()
_settings = get_settings()
_SELF_PATH = "/v1/opds/library"


def opds_service() -> OpdsCatalogService:
    return _opds_service


@router.get("/library", response_class=Response)
async def personal_library_opds(
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[OpdsCatalogService, Depends(opds_service)],
) -> Response:
    payload = await service.render(
        principal_id=identity.principal_id,
        self_url=_SELF_PATH,
        web_origin=_settings.web_origin,
    )
    return Response(
        content=payload,
        media_type=OPDS_MEDIA_TYPE,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="bukmatika-library.xml"',
            "X-Content-Type-Options": "nosniff",
            "Vary": "Cookie",
        },
    )


__all__ = ["opds_service", "router"]
