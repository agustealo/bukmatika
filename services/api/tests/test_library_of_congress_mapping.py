from bukmatika.discovery.library_of_congress import LibraryOfCongressAdapter
from bukmatika.domain import RightsState


def test_loc_digitized_pdf_does_not_become_download_authority() -> None:
    record = LibraryOfCongressAdapter._record(
        {
            "id": "http://www.loc.gov/item/2020123456/",
            "title": "A Digitized Book",
            "contributor": ["Jane Author"],
            "language": ["english"],
            "date": "1899",
            "digitized": True,
        },
        {
            "item": {
                "id": "https://www.loc.gov/item/2020123456/",
                "title": "A Digitized Book",
                "contributor_names": ["Jane Author"],
                "language": ["english"],
                "date_issued": "1899",
                "library_of_congress_control_number": "2020123456",
                "rights": ["Rights status requires research"],
                "access_restricted": False,
            },
            "resources": [
                {
                    "pdf": (
                        "https://tile.loc.gov/storage-services/service/"
                        "gdc/gdclccn/20/20/12/34/56/book.pdf"
                    )
                }
            ],
        },
    )

    assert record is not None
    candidate = record.candidate
    assert candidate.formats == ["PDF"]
    assert candidate.rights[0].state is RightsState.UNKNOWN
    assert candidate.assets[0].rights[0].state is RightsState.UNKNOWN
    assert candidate.identifiers["lccn"] == ["2020123456"]


def test_loc_image_urls_map_to_covers_with_loc_host_fence() -> None:
    record = LibraryOfCongressAdapter._record(
        {
            "id": "https://www.loc.gov/item/cover-test/",
            "title": "LOC Cover Test",
        },
        {
            "item": {
                "id": "https://www.loc.gov/item/cover-test/",
                "title": "LOC Cover Test",
            },
            "resources": [],
            "image_url": [
                "http://tile.loc.gov/storage-services/service/cover-test/cover.jpg",
                "https://cdn.loc.gov/cover-test/cover.png",
                "https://evil.example/not-allowed.jpg",
            ],
        },
    )

    assert record is not None
    assert [str(cover.url) for cover in record.candidate.covers] == [
        "https://tile.loc.gov/storage-services/service/cover-test/cover.jpg",
        "https://cdn.loc.gov/cover-test/cover.png",
    ]
    assert [cover.media_type for cover in record.candidate.covers] == [
        "image/jpeg",
        "image/png",
    ]
    assert record.candidate.assets == []


def test_loc_access_restriction_outranks_resource_presence() -> None:
    record = LibraryOfCongressAdapter._record(
        {
            "id": "https://www.loc.gov/item/restricted-book/",
            "title": "Restricted Book",
        },
        {
            "item": {
                "id": "https://www.loc.gov/item/restricted-book/",
                "title": "Restricted Book",
                "access_restricted": True,
            },
            "resources": [
                {
                    "pdf": "https://tile.loc.gov/storage-services/service/restricted/book.pdf"
                }
            ],
        },
    )

    assert record is not None
    assert record.candidate.assets
    assert record.candidate.rights[0].state is RightsState.RESTRICTED
    assert record.candidate.assets[0].rights[0].state is RightsState.RESTRICTED


def test_loc_rejects_non_loc_item_urls_for_hydration() -> None:
    assert LibraryOfCongressAdapter._item_url("https://evil.example/item/123/") is None
