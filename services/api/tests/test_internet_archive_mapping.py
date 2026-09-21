from bukmatika.discovery.internet_archive import InternetArchiveAdapter
from bukmatika.domain import RightsState


def test_internet_archive_exact_metadata_exposes_supported_assets_without_authorizing_them(
) -> None:
    record = InternetArchiveAdapter._record(
        {"identifier": "example-book", "title": "Example Book"},
        {
            "metadata": {
                "identifier": "example-book",
                "title": "Example Book",
                "creator": "Jane Writer",
                "date": "1899",
            },
            "files": [
                {
                    "name": "example-book.pdf",
                    "format": "Text PDF",
                    "source": "original",
                    "size": "1234",
                    "sha1": "abc",
                },
                {"name": "cover.jpg", "format": "JPEG"},
            ],
        },
    )

    assert record is not None
    assert record.candidate.record_kind == "edition"
    assert record.candidate.formats == ["PDF"]
    assert record.candidate.assets[0].size_bytes == 1234
    assert record.candidate.rights[0].state is RightsState.UNKNOWN


def test_internet_archive_restricted_item_outranks_file_presence() -> None:
    record = InternetArchiveAdapter._record(
        {"identifier": "restricted-book", "title": "Restricted Book"},
        {
            "metadata": {
                "title": "Restricted Book",
                "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
            },
            "access-restricted-item": True,
            "files": [{"name": "restricted-book.pdf", "format": "Text PDF"}],
        },
    )

    assert record is not None
    assert record.candidate.assets
    assert record.candidate.rights[0].state is RightsState.RESTRICTED


def test_internet_archive_recognized_creative_commons_uri_is_open_license_evidence() -> None:
    record = InternetArchiveAdapter._record(
        {"identifier": "open-book", "title": "Open Book"},
        {
            "metadata": {
                "title": "Open Book",
                "licenseurl": "https://creativecommons.org/licenses/by/4.0/",
            },
            "files": [],
        },
    )

    assert record is not None
    assert record.candidate.rights[0].state is RightsState.OPEN_LICENSE
