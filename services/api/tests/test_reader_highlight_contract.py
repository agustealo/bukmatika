from uuid import uuid4

import pytest
from pydantic import ValidationError

from bukmatika.main import app
from bukmatika.reader import HighlightCreate


def test_highlight_contract_requires_positive_exact_range() -> None:
    section_id = uuid4()

    with pytest.raises(ValidationError):
        HighlightCreate(section_id=section_id, char_start=3, char_end=3)
    with pytest.raises(ValidationError):
        HighlightCreate(section_id=section_id, char_start=4, char_end=2)

    valid = HighlightCreate(
        section_id=section_id,
        char_start=2,
        char_end=9,
        note="Coordinate-backed note",
    )
    assert valid.char_start == 2
    assert valid.char_end == 9


def test_highlight_routes_are_mounted_in_openapi_contract() -> None:
    paths = app.openapi()["paths"]
    prefix = "/v1/library/{library_entry_id}/documents/{document_id}"
    assert f"{prefix}/highlights" in paths
    assert "post" in paths[f"{prefix}/highlights"]
    assert f"{prefix}/highlights/{{highlight_id}}/note" in paths
    assert "post" in paths[f"{prefix}/highlights/{{highlight_id}}/note"]
    assert f"{prefix}/highlights/{{highlight_id}}/remove" in paths
    assert "post" in paths[f"{prefix}/highlights/{{highlight_id}}/remove"]
