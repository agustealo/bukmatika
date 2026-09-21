from bukmatika.discovery.openlibrary import OpenLibraryAdapter
from bukmatika.domain import RightsState


def test_openlibrary_public_readability_does_not_become_copyright_authority() -> None:
    candidate = OpenLibraryAdapter._candidate(
        {
            "key": "/works/OL1W",
            "title": "A Historical Work",
            "author_name": ["A. Writer"],
            "first_publish_year": 1890,
            "language": ["eng"],
            "edition_key": ["OL1M"],
            "ebook_access": "public",
            "public_scan_b": True,
        }
    )
    assert candidate is not None
    assert candidate.title == "A Historical Work"
    assert candidate.rights[0].state is RightsState.UNKNOWN


def test_openlibrary_borrowable_never_maps_to_download_authority() -> None:
    candidate = OpenLibraryAdapter._candidate(
        {
            "key": "/works/OL2W",
            "title": "Borrowed Work",
            "ebook_access": "borrowable",
        }
    )
    assert candidate is not None
    assert candidate.rights[0].state is RightsState.BORROW_ONLY
