from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bukmatika.library.portability_domain import (
    PortableDocumentIdentity,
    PortableReadingState,
    PortableSmartShelf,
)


def _document() -> PortableDocumentIdentity:
    return PortableDocumentIdentity(
        source_document_id=uuid4(),
        source_sha256="a" * 64,
        format="EPUB",
        parser_name="epub",
        parser_version="1",
    )


def test_portable_reading_state_rejects_noncanonical_status() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError):
        PortableReadingState.model_validate(
            {
                "source_reading_state_id": uuid4(),
                "document": _document().model_dump(),
                "status": "paused",
                "progress_fraction": 0.5,
                "position": None,
                "last_read_at": now,
                "created_at": now,
                "updated_at": now,
                "bookmarks": [],
                "highlights": [],
            }
        )


def test_portable_smart_shelf_rejects_noncanonical_reading_status() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError):
        PortableSmartShelf.model_validate(
            {
                "source_smart_shelf_id": uuid4(),
                "name": "Paused books",
                "description": None,
                "reading_status": "paused",
                "collection_id": None,
                "tag_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )


def test_portable_smart_shelf_requires_at_least_one_rule() -> None:
    now = datetime.now(UTC)

    with pytest.raises(ValidationError):
        PortableSmartShelf.model_validate(
            {
                "source_smart_shelf_id": uuid4(),
                "name": "Ruleless",
                "description": None,
                "reading_status": None,
                "collection_id": None,
                "tag_id": None,
                "created_at": now,
                "updated_at": now,
            }
        )
