import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, suppress
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.acquisition.storage import StoredObjectPathResolver
from bukmatika.config import Settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.document_models import Document
from bukmatika.persistence.documents import (
    DocumentRepository,
    DocumentSource,
    DocumentSourceChanged,
)
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.processing.chunking import chunk_sections
from bukmatika.processing.domain import (
    DocumentResponse,
    DocumentSearchHit,
    DocumentSearchResponse,
)
from bukmatika.processing.parsers import (
    DocumentParseError,
    DocumentParser,
    DocumentRequiresOCR,
    ParserRegistry,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class ProcessingAssetNotFound(LookupError):
    pass


class AssetNotStored(RuntimeError):
    pass


class StoredObjectUnavailable(RuntimeError):
    pass


class ProcessingTimedOut(RuntimeError):
    pass


class ProcessingSourceChanged(RuntimeError):
    pass


class ProcessedDocumentNotFound(LookupError):
    pass


class DocumentProcessingService:
    """Parse verified local objects into the canonical normalized document model."""

    def __init__(
        self,
        registry: ParserRegistry,
        object_store: StoredObjectPathResolver,
        settings: Settings,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._registry = registry
        self._object_store = object_store
        self._settings = settings
        self._session_scope = session_scope_factory

    async def process_asset(self, asset_id: UUID) -> DocumentResponse:
        source = await self._load_source(asset_id)
        parser = self._registry.get(source.format)
        await self._set_processing_state(source, parser, status="processing")
        await self._record_processing_started(source)

        try:
            if source.byte_size > self._settings.processing_max_bytes:
                raise DocumentParseError("Stored object exceeds configured processing byte limit")
            try:
                path = await self._object_store.resolve_path(source.storage_key)
            except (FileNotFoundError, ValueError) as exc:
                raise StoredObjectUnavailable("Verified stored object is unavailable") from exc

            try:
                parsed = await asyncio.wait_for(
                    asyncio.to_thread(
                        parser.parse,
                        path,
                        max_bytes=self._settings.processing_max_bytes,
                    ),
                    timeout=self._settings.processing_timeout_seconds,
                )
            except TimeoutError as exc:
                raise ProcessingTimedOut("Document parsing exceeded configured timeout") from exc

            chunks = chunk_sections(parsed.sections)
            async with self._session_scope() as database_session:
                repository = DocumentRepository(database_session)
                try:
                    document = await repository.persist_document(
                        source=source,
                        parsed=parsed,
                        chunks=chunks,
                    )
                    await repository.set_processing_state(
                        source=source,
                        processor_name=parsed.parser_name,
                        processor_version=parsed.parser_version,
                        status="completed",
                    )
                except DocumentSourceChanged as exc:
                    raise ProcessingSourceChanged(str(exc)) from exc
                await InteractionEventRepository(database_session).record(
                    SemanticEventType.DOCUMENT_PROCESSING_COMPLETED,
                    entity_type="document",
                    entity_id=document.id,
                    context={
                        "asset_id": str(source.asset_id),
                        "stored_object_id": str(source.stored_object_id),
                        "source_sha256": source.sha256,
                        "format": source.format.upper(),
                        "parser_name": parsed.parser_name,
                        "parser_version": parsed.parser_version,
                        "section_count": document.section_count,
                        "chunk_count": document.chunk_count,
                    },
                )
            return _document_response(document)
        except DocumentRequiresOCR as exc:
            await self._set_processing_state(
                source,
                parser,
                status="requires_ocr",
                error_code="DOCUMENT_REQUIRES_OCR",
                error_detail=str(exc),
            )
            await self._record_processing_failed(source, exc)
            raise
        except Exception as exc:
            await self._set_failed_state(source, parser, exc)
            await self._record_processing_failed(source, exc)
            raise

    async def search_document(
        self,
        document_id: UUID,
        *,
        query: str,
        limit: int,
    ) -> DocumentSearchResponse:
        normalized_query = " ".join(query.split())
        if not normalized_query:
            raise ValueError("Document search query cannot be empty")

        async with self._session_scope() as database_session:
            repository = DocumentRepository(database_session)
            document = await repository.get_document(document_id)
            if document is None:
                raise ProcessedDocumentNotFound(f"Document {document_id} does not exist")
            events = InteractionEventRepository(database_session)
            await events.record(
                SemanticEventType.DOCUMENT_SEARCH_SUBMITTED,
                entity_type="document",
                entity_id=document_id,
                context={"query": normalized_query, "limit": limit},
            )
            matches = await repository.search(
                document_id=document_id,
                query=normalized_query,
                limit=limit,
            )
            await events.record(
                SemanticEventType.DOCUMENT_SEARCH_COMPLETED,
                entity_type="document",
                entity_id=document_id,
                context={"query": normalized_query, "result_count": len(matches)},
            )

        return DocumentSearchResponse(
            document_id=document_id,
            query=normalized_query,
            items=[
                DocumentSearchHit(
                    chunk_id=match.chunk_id,
                    section_id=match.section_id,
                    section_ordinal=match.section_ordinal,
                    chunk_ordinal=match.chunk_ordinal,
                    heading=match.heading,
                    locator=match.locator,
                    char_start=match.char_start,
                    char_end=match.char_end,
                    text=match.text,
                    score=max(0.0, match.score),
                )
                for match in matches
            ],
        )

    async def _load_source(self, asset_id: UUID) -> DocumentSource:
        async with self._session_scope() as database_session:
            repository = DocumentRepository(database_session)
            source = await repository.source_for_asset(asset_id)
            if source is not None:
                return source
            if await repository.asset_exists(asset_id):
                raise AssetNotStored(f"Asset {asset_id} has no verified stored object")
            raise ProcessingAssetNotFound(f"Asset {asset_id} does not exist")

    async def _set_processing_state(
        self,
        source: DocumentSource,
        parser: DocumentParser,
        *,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        async with self._session_scope() as database_session:
            try:
                await DocumentRepository(database_session).set_processing_state(
                    source=source,
                    processor_name=parser.name,
                    processor_version=parser.version,
                    status=status,
                    error_code=error_code,
                    error_detail=error_detail,
                )
            except DocumentSourceChanged as exc:
                raise ProcessingSourceChanged(str(exc)) from exc

    async def _set_failed_state(
        self,
        source: DocumentSource,
        parser: DocumentParser,
        error: Exception,
    ) -> None:
        with suppress(ProcessingSourceChanged):
            await self._set_processing_state(
                source,
                parser,
                status="failed",
                error_code=type(error).__name__.upper(),
                error_detail=str(error)[:2000],
            )

    async def _record_processing_started(self, source: DocumentSource) -> None:
        async with self._session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DOCUMENT_PROCESSING_STARTED,
                entity_type="asset",
                entity_id=source.asset_id,
                context={
                    "stored_object_id": str(source.stored_object_id),
                    "source_sha256": source.sha256,
                    "format": source.format.upper(),
                },
            )

    async def _record_processing_failed(self, source: DocumentSource, error: Exception) -> None:
        async with self._session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                SemanticEventType.DOCUMENT_PROCESSING_FAILED,
                entity_type="asset",
                entity_id=source.asset_id,
                context={
                    "stored_object_id": str(source.stored_object_id),
                    "source_sha256": source.sha256,
                    "format": source.format.upper(),
                    "error_type": type(error).__name__,
                },
            )


def _document_response(document: Document) -> DocumentResponse:
    return DocumentResponse(
        document_id=document.id,
        asset_id=document.asset_id,
        stored_object_id=document.stored_object_id,
        source_sha256=document.source_sha256,
        format=document.format,
        parser_name=document.parser_name,
        parser_version=document.parser_version,
        section_count=document.section_count,
        chunk_count=document.chunk_count,
    )
