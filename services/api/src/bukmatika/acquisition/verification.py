import zipfile
from pathlib import Path, PurePosixPath


class FormatVerificationError(RuntimeError):
    pass


_ALLOWED_MEDIA_TYPES: dict[str, set[str]] = {
    "DOC": {"application/msword", "application/octet-stream"},
    "DOCX": {
        "application/octet-stream",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "EPUB": {"application/epub+zip", "application/octet-stream", "application/zip"},
    "HTML": {"application/xhtml+xml", "text/html", "application/octet-stream"},
    "PDF": {"application/pdf", "application/octet-stream"},
    "TXT": {"application/octet-stream", "text/plain"},
}


def verify_download(
    path: Path,
    *,
    expected_format: str,
    media_type: str | None,
    archive_max_members: int,
    archive_max_uncompressed_bytes: int,
    archive_max_compression_ratio: float,
) -> None:
    format_name = expected_format.upper()
    allowed_media_types = _ALLOWED_MEDIA_TYPES.get(format_name)
    if allowed_media_types is None:
        raise FormatVerificationError(f"Unsupported acquisition format: {expected_format}")
    if media_type is not None and media_type not in allowed_media_types:
        raise FormatVerificationError(
            f"Content-Type {media_type!r} does not match expected {format_name} asset"
        )

    with path.open("rb") as handle:
        sample = handle.read(65536)

    if format_name == "PDF":
        if b"%PDF-" not in sample[:1024]:
            raise FormatVerificationError("Downloaded asset is not a PDF")
        return
    if format_name == "DOC":
        if not sample.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise FormatVerificationError("Downloaded asset is not an OLE Word document")
        return
    if format_name == "HTML":
        lowered = sample.lstrip().lower()
        if not (
            lowered.startswith(b"<!doctype html")
            or lowered.startswith(b"<html")
            or b"<html" in lowered[:4096]
        ):
            raise FormatVerificationError("Downloaded asset is not recognizable HTML")
        return
    if format_name == "TXT":
        if b"\x00" in sample:
            raise FormatVerificationError("Downloaded text asset contains binary NUL bytes")
        return

    _verify_zip_container(
        path,
        format_name=format_name,
        max_members=archive_max_members,
        max_uncompressed_bytes=archive_max_uncompressed_bytes,
        max_compression_ratio=archive_max_compression_ratio,
    )


def _verify_zip_container(
    path: Path,
    *,
    format_name: str,
    max_members: int,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> None:
    if not zipfile.is_zipfile(path):
        raise FormatVerificationError(f"Downloaded asset is not a valid {format_name} container")
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > max_members:
                raise FormatVerificationError("Archive member count exceeds configured limit")
            total_uncompressed = 0
            names: set[str] = set()
            for member in members:
                _validate_member_name(member.filename)
                names.add(member.filename)
                total_uncompressed += member.file_size
                if total_uncompressed > max_uncompressed_bytes:
                    raise FormatVerificationError(
                        "Archive uncompressed size exceeds configured limit"
                    )
                if member.file_size > 0:
                    compressed = max(member.compress_size, 1)
                    if member.file_size / compressed > max_compression_ratio:
                        raise FormatVerificationError(
                            "Archive compression ratio exceeds configured limit"
                        )

            if format_name == "EPUB":
                if "mimetype" not in names:
                    raise FormatVerificationError("EPUB is missing the required mimetype entry")
                info = archive.getinfo("mimetype")
                if info.file_size > 256:
                    raise FormatVerificationError("EPUB mimetype entry is unexpectedly large")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise FormatVerificationError("EPUB mimetype entry must be stored uncompressed")
                if archive.read("mimetype") != b"application/epub+zip":
                    raise FormatVerificationError("EPUB mimetype entry is invalid")
                return

            if format_name == "DOCX":
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise FormatVerificationError("DOCX container is missing required Word parts")
                return

            raise FormatVerificationError(f"Unsupported ZIP-based format: {format_name}")
    except FormatVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise FormatVerificationError("Archive verification failed") from exc


def _validate_member_name(name: str) -> None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise FormatVerificationError("Archive contains an unsafe member path")
