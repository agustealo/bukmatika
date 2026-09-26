from bukmatika.discovery.gutenberg import ProjectGutenbergAdapter
from bukmatika.domain import RightsState


def test_gutenberg_opds_acquisition_links_map_to_authorized_assets() -> None:
    payload = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/12345</id>
        <title>Example History</title>
        <author><name>Jane Historian</name></author>
        <category term="en" scheme="http://purl.org/dc/terms/language" />
        <category term="History" scheme="http://purl.org/dc/terms/LCSH" />
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="application/epub+zip"
              href="https://www.gutenberg.org/ebooks/12345.epub3.images" />
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="text/plain; charset=utf-8"
              href="https://www.gutenberg.org/ebooks/12345.txt.utf-8" />
      </entry>
    </feed>'''

    records = ProjectGutenbergAdapter._parse_feed(payload)

    assert len(records) == 1
    candidate = records[0].candidate
    assert candidate.source_record_id == "12345"
    assert candidate.record_kind == "edition"
    assert candidate.authors == ["Jane Historian"]
    assert candidate.languages == ["en"]
    assert candidate.subjects == ["History"]
    assert candidate.formats == ["EPUB", "TXT"]
    assert candidate.rights[0].state is RightsState.AUTHORIZED_DOWNLOAD
    assert all(
        asset.rights[0].state is RightsState.AUTHORIZED_DOWNLOAD for asset in candidate.assets
    )


def test_gutenberg_image_links_map_to_covers_not_acquisition_assets() -> None:
    payload = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/54321</id>
        <title>Covered Gutenberg Work</title>
        <link rel="http://opds-spec.org/image"
              type="image/jpeg"
              href="https://www.gutenberg.org/cache/epub/54321/pg54321.cover.medium.jpg" />
        <link rel="http://opds-spec.org/image/thumbnail"
              type="image/jpeg"
              href="https://www.gutenberg.org/cache/epub/54321/pg54321.cover.small.jpg" />
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="application/epub+zip"
              href="https://www.gutenberg.org/ebooks/54321.epub3.images" />
      </entry>
    </feed>'''

    records = ProjectGutenbergAdapter._parse_feed(payload)

    candidate = records[0].candidate
    assert [cover.kind for cover in candidate.covers] == ["cover", "thumbnail"]
    assert [str(cover.url) for cover in candidate.covers] == [
        "https://www.gutenberg.org/cache/epub/54321/pg54321.cover.medium.jpg",
        "https://www.gutenberg.org/cache/epub/54321/pg54321.cover.small.jpg",
    ]
    assert len(candidate.assets) == 1
    assert str(candidate.assets[0].url) == "https://www.gutenberg.org/ebooks/54321.epub3.images"
    assert records[0].source_payload["cover_links"][0]["rel"] == "http://opds-spec.org/image"


def test_gutenberg_rejects_non_provider_cover_hosts() -> None:
    payload = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/67890</id>
        <title>Host Fenced Cover</title>
        <link rel="http://opds-spec.org/image"
              type="image/jpeg"
              href="https://evil.example/cover.jpg" />
      </entry>
    </feed>'''

    records = ProjectGutenbergAdapter._parse_feed(payload)

    assert records[0].candidate.covers == []
    assert records[0].candidate.assets == []


def test_gutenberg_entry_without_supported_acquisition_stays_unknown() -> None:
    payload = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>https://www.gutenberg.org/ebooks/98765</id>
        <title>Metadata Only</title>
        <link rel="alternate" type="text/html"
              href="https://www.gutenberg.org/ebooks/98765" />
      </entry>
    </feed>'''

    records = ProjectGutenbergAdapter._parse_feed(payload)

    assert records[0].candidate.assets == []
    assert records[0].candidate.rights[0].state is RightsState.UNKNOWN
