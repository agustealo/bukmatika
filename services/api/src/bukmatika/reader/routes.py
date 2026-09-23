from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence.readers import (
    ReaderAccessDenied,
    ReaderBookmarkNotFound,
    ReaderHighlightNotFound,
    ReaderPositionInvalid,
)
from bukmatika.reader.domain import (
    BookmarkCreate,
    BookmarkResponse,
    HighlightCreate,
    HighlightNoteUpdate,
    HighlightResponse,
    ReaderDocumentResponse,
    ReadingProgressUpdate,
    ReadingStateResponse,
)
from bukmatika.reader.service import ReaderService

router = APIRouter(prefix="/v1/library/{library_entry_id}/documents/{document_id}")
_reader_service = ReaderService()


def reader_service() -> ReaderService:
    return _reader_service


@router.get("/reader", response_model=ReaderDocumentResponse)
async def open_reader(
    library_entry_id: UUID,
    document_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
    after: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 12,
) -> ReaderDocumentResponse:
    try:
        return await service.open_reader(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            after_ordinal=after,
            limit=limit,
        )
    except ReaderAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader document not found",
        ) from exc


@router.post("/progress", response_model=ReadingStateResponse)
async def save_progress(
    library_entry_id: UUID,
    document_id: UUID,
    update: ReadingProgressUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> ReadingStateResponse:
    try:
        return await service.save_progress(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            update=update,
        )
    except ReaderAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader document not found",
        ) from exc
    except ReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "READER_POSITION_INVALID"},
        ) from exc


@router.post("/bookmarks", response_model=BookmarkResponse, status_code=status.HTTP_201_CREATED)
async def add_bookmark(
    library_entry_id: UUID,
    document_id: UUID,
    create: BookmarkCreate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> BookmarkResponse:
    try:
        return await service.add_bookmark(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            create=create,
        )
    except ReaderAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader document not found",
        ) from exc
    except ReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "READER_POSITION_INVALID"},
        ) from exc


@router.post("/bookmarks/{bookmark_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_bookmark(
    library_entry_id: UUID,
    document_id: UUID,
    bookmark_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> Response:
    try:
        await service.remove_bookmark(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            bookmark_id=bookmark_id,
        )
    except (ReaderAccessDenied, ReaderBookmarkNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bookmark not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/highlights",
    response_model=HighlightResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_highlight(
    library_entry_id: UUID,
    document_id: UUID,
    create: HighlightCreate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> HighlightResponse:
    try:
        return await service.add_highlight(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            create=create,
        )
    except ReaderAccessDenied as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reader document not found",
        ) from exc
    except ReaderPositionInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "READER_HIGHLIGHT_RANGE_INVALID"},
        ) from exc


@router.post("/highlights/{highlight_id}/note", response_model=HighlightResponse)
async def update_highlight_note(
    library_entry_id: UUID,
    document_id: UUID,
    highlight_id: UUID,
    update: HighlightNoteUpdate,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> HighlightResponse:
    try:
        return await service.update_highlight_note(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            highlight_id=highlight_id,
            update=update,
        )
    except (ReaderAccessDenied, ReaderHighlightNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Highlight not found",
        ) from exc


@router.post("/highlights/{highlight_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_highlight(
    library_entry_id: UUID,
    document_id: UUID,
    highlight_id: UUID,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    service: Annotated[ReaderService, Depends(reader_service)],
) -> Response:
    try:
        await service.remove_highlight(
            principal_id=identity.principal_id,
            library_entry_id=library_entry_id,
            document_id=document_id,
            highlight_id=highlight_id,
        )
    except (ReaderAccessDenied, ReaderHighlightNotFound) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Highlight not found",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
