from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import Asset, StoredObject
from bukmatika.processing.domain import ParsedChunk, ParsedDocument


class DocumentSourceChanged(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DocumentSource:
    asset_id: UUID
    stored_object_id: UUID
    storage_key: str
    sha256: str
    byte_size: int
    format: str


@dataclass(frozen=True, slots=True)
class DocumentSearchMatch:
    chunk_id: UUID
    section_id: UUID
    section_ordinal: int
    chunk_ordinal: int
    heading: str | None
    locator: dict[str, Any]
    char_start: int
    char_end: int
    text: str
    score: float


class DocumentRepository:
    """Canonical persistence authority for parsed documents and document search."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def source_for_asset(self, asset_id: UUID) -> DocumentSource | None:
        row = (
            await self._session.execute(
                select(Asset, StoredObject)
                .join(StoredObject, Asset.stored_object_id == StoredObject.id)
                .where(Asset.id == asset_id)
            )
        ).one_or_none()
        if row is None:
            return None
        asset, stored = row
        return DocumentSource(
            asset_id=asset.id,
            stored_object_id=stored.id,
            storage_key=stored.storage_key,
            sha256=stored.sha256,
            byte_size=stored.byte_size,
            format=asset.format,
        )

    async def asset_exists(self, asset_id: UUID) -> bool:
        value = await self._session.scalar(select(Asset.id).where(Asset.id == asset_id))
        return value is not None

    async def get_document(self, document_id: UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def get_document_for_asset(self, asset_id: UUID) -> Document | None:
        return await self._session.scalar(select(Document).where(Document.asset_id == asset_id))

    async def persist_document(
        self,
        *,
        source: DocumentSource,
        parsed: ParsedDocument,
        chunks: tuple[ParsedChunk, ...],
    ) -> Document:
        asset = await self._session.scalar(
            select(Asset).where(Asset.id == source.asset_id).with_for_update()
        )
        if asset is None:
            raise DocumentSourceChanged("Asset disappeared while document was being processed")
        if asset.stored_object_id != source.stored_object_id:
            raise DocumentSourceChanged("Stored object changed while document was being processed")

        existing = await self.get_document_for_asset(source.asset_id)
        if (
            existing is not None
            and existing.stored_object_id == source.stored_object_id
            and existing.source_sha256 == source.sha256
            and existing.parser_name == parsed.parser_name
            and existing.parser_version == parsed.parser_version
        ):
            return existing

        if existing is not None:
            await self._session.execute(delete(Document).where(Document.id == existing.id))
            await self._session.flush()

        document = Document(
            asset_id=source.asset_id,
            stored_object_id=source.stored_object_id,
            source_sha256=source.sha256,
            format=source.format.upper(),
            parser_name=parsed.parser_name,
            parser_version=parsed.parser_version,
            section_count=len(parsed.sections),
            chunk_count=len(chunks),
        )
        self._session.add(document)
        await self._session.flush()

        sections: dict[int, DocumentSection] = {}
        for parsed_section in parsed.sections:
            persisted_section = DocumentSection(
                document_id=document.id,
                ordinal=parsed_section.ordinal,
                heading=parsed_section.heading,
                locator=parsed_section.locator,
                text=parsed_section.text,
            )
            self._session.add(persisted_section)
            sections[parsed_section.ordinal] = persisted_section
        await self._session.flush()

        for chunk in chunks:
            persisted_section = sections.get(chunk.section_ordinal)
            if persisted_section is None:
                raise RuntimeError("Chunk references an unknown document section")
            self._session.add(
                DocumentChunk(
                    document_id=document.id,
                    section_id=persisted_section.id,
                    ordinal=chunk.ordinal,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    text=chunk.text,
                )
            )
        await self._session.flush()
        return document

    async def search(
        self,
        *,
        document_id: UUID,
        query: str,
        limit: int,
    ) -> list[DocumentSearchMatch]:
        configuration: ColumnElement[Any] = literal_column("'simple'::regconfig")
        tsquery = func.websearch_to_tsquery(configuration, query)
        rank = func.ts_rank_cd(DocumentChunk.search_vector, tsquery).label("score")
        rows = (
            await self._session.execute(
                select(DocumentChunk, DocumentSection, rank)
                .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
                .where(
                    DocumentChunk.document_id == document_id,
                    DocumentChunk.search_vector.op("@@")(tsquery),
                )
                .order_by(rank.desc(), DocumentChunk.ordinal)
                .limit(limit)
            )
        ).all()
        return [
            DocumentSearchMatch(
                chunk_id=chunk.id,
                section_id=section.id,
                section_ordinal=section.ordinal,
                chunk_ordinal=chunk.ordinal,
                heading=section.heading,
                locator=section.locator,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                text=chunk.text,
                score=float(score),
            )
            for chunk, section, score in rows
        ]
