from uuid import uuid4

import pytest
from pydantic import ValidationError

from bukmatika.main import app
from bukmatika.research import ResearchCompareRequest


def test_compare_request_requires_two_to_six_unique_sources() -> None:
    source_ids = [uuid4() for _ in range(7)]

    with pytest.raises(ValidationError):
        ResearchCompareRequest(query="trade", library_entry_ids=source_ids[:1])
    with pytest.raises(ValidationError):
        ResearchCompareRequest(query="trade", library_entry_ids=[source_ids[0], source_ids[0]])
    with pytest.raises(ValidationError):
        ResearchCompareRequest(query="trade", library_entry_ids=source_ids)

    valid = ResearchCompareRequest(
        query="  maritime   trade  ",
        library_entry_ids=source_ids[:6],
        per_source_limit=5,
    )
    assert valid.query == "maritime trade"
    assert valid.library_entry_ids == source_ids[:6]
    assert valid.per_source_limit == 5


def test_compare_route_is_mounted() -> None:
    methods_by_path = {
        route.path: getattr(route, "methods", set())
        for route in app.routes
    }
    assert "/v1/research/compare" in methods_by_path
    assert "POST" in methods_by_path["/v1/research/compare"]
