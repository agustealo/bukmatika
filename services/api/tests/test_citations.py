import json
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_library import _seed_dossier

from bukmatika.library.citation import (
    CitationExportNotFound,
    CitationExportService,
    CitationFormat,
)
from bukmatika.persistence.models import Identifier


async def test_citation_exports_project_canonical_edition_metadata(
    session: AsyncSession,
) -> None:
    _, work, edition, _, _, _ = await _seed_dossier(session, suffix="citation")
    edition.title = "Edition citation & archives"
    edition.publisher = "Archive Press & Co."
    edition.edition_statement = "Second edition"
    isbn = "9780000001894"
    session.add(
        Identifier(
            entity_type="edition",
            entity_id=edition.id,
            scheme="isbn",
            value=isbn,
            normalized_value=isbn,
        )
    )
    await session.flush()

    service = CitationExportService(session)

    csl = await service.export(
        work_id=work.id,
        edition_id=edition.id,
        citation_format=CitationFormat.CSL_JSON,
    )
    assert csl.media_type == "application/vnd.citationstyles.csl+json"
    assert csl.filename.endswith(".json")
    csl_payload = json.loads(csl.content)
    assert csl_payload == [
        {
            "ISBN": isbn,
            "author": [{"literal": "Author citation"}],
            "edition": "Second edition",
            "id": f"bukmatika:edition:{edition.id}",
            "issued": {"date-parts": [[1898]]},
            "language": "en",
            "publisher": "Archive Press & Co.",
            "title": "Edition citation & archives",
            "type": "book",
        }
    ]

    bibtex = await service.export(
        work_id=work.id,
        edition_id=edition.id,
        citation_format=CitationFormat.BIBTEX,
    )
    assert bibtex.media_type == "application/x-bibtex"
    assert bibtex.filename.endswith(".bib")
    assert "@book{" in bibtex.content
    assert "author = {Author citation}" in bibtex.content
    assert "title = {Edition citation \\& archives}" in bibtex.content
    assert "publisher = {Archive Press \\& Co.}" in bibtex.content
    assert f"isbn = {{{isbn}}}" in bibtex.content
    assert "year = {1898}" in bibtex.content

    ris = await service.export(
        work_id=work.id,
        edition_id=edition.id,
        citation_format=CitationFormat.RIS,
    )
    assert ris.media_type == "application/x-research-info-systems"
    assert ris.filename.endswith(".ris")
    assert ris.content.startswith("TY  - BOOK\r\n")
    assert "TI  - Edition citation & archives\r\n" in ris.content
    assert "AU  - Author citation\r\n" in ris.content
    assert "PB  - Archive Press & Co.\r\n" in ris.content
    assert "PY  - 1898\r\n" in ris.content
    assert f"SN  - {isbn}\r\n" in ris.content
    assert ris.content.endswith("ER  -\r\n")


async def test_citation_export_rejects_edition_from_another_work(
    session: AsyncSession,
) -> None:
    _, _, edition, _, _, _ = await _seed_dossier(session, suffix="citation-mismatch")

    with pytest.raises(CitationExportNotFound, match="not part of this canonical work"):
        await CitationExportService(session).export(
            work_id=uuid4(),
            edition_id=edition.id,
            citation_format=CitationFormat.CSL_JSON,
        )


async def test_citation_export_collapses_embedded_line_breaks(
    session: AsyncSession,
) -> None:
    _, work, edition, _, _, _ = await _seed_dossier(session, suffix="citation-lines")
    edition.title = "Title\nwith injected line"
    edition.publisher = "Publisher\r\nwith another line"
    await session.flush()

    ris = await CitationExportService(session).export(
        work_id=work.id,
        edition_id=edition.id,
        citation_format=CitationFormat.RIS,
    )

    assert "TI  - Title with injected line\r\n" in ris.content
    assert "PB  - Publisher with another line\r\n" in ris.content
    assert "\nwith injected line" not in ris.content
