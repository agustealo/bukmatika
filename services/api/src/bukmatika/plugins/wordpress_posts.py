"""Bounded, source-owned, authenticated WordPress post discovery. No writes."""

import base64
import json
from dataclasses import dataclass

from bukmatika.plugins.pinned_https import get_wordpress_rest

_POST_STATUSES = frozenset({"publish", "draft", "pending", "private", "future"})
_PAGE_SIZE = 20


class WordPressPostReadUnavailable(RuntimeError):
    """The remote post list cannot be trusted or reached."""


class WordPressPostReadDenied(PermissionError):
    """The WordPress account cannot read the requested posts."""


@dataclass(frozen=True, slots=True)
class WordPressPostSummary:
    id: int
    status: str
    slug: str
    title: str


def fetch_wordpress_posts(
    site_url: str, username: str, application_password: str, *, page: int
) -> tuple[WordPressPostSummary, ...]:
    """Get one fixed-size page; never accept arbitrary routes or fields."""
    if (
        type(page) is not int
        or not 1 <= page <= 50
        or not username
        or not application_password
        or any(char in username for char in (":", "\r", "\n"))
    ):
        raise WordPressPostReadUnavailable("WordPress post request invalid")
    token = base64.b64encode(
        f"{username}:{application_password}".encode("utf-8")
    ).decode("ascii")
    route = (
        f"/wp-json/wp/v2/posts?context=edit&per_page={_PAGE_SIZE}"
        f"&page={page}&_fields=id,status,slug,title"
    )
    try:
        response = get_wordpress_rest(
            site_url, route, authorization=f"Basic {token}", max_bytes=262144
        )
    except (OSError, ValueError):
        raise WordPressPostReadUnavailable("WordPress post read unavailable") from None
    if response.status_code in (401, 403):
        raise WordPressPostReadDenied("WordPress denied access to posts")
    if response.status_code != 200:
        raise WordPressPostReadUnavailable("WordPress post read unavailable")
    try:
        raw = json.loads(response.body)
    except (ValueError, UnicodeError):
        raise WordPressPostReadUnavailable("WordPress post response invalid") from None
    if not isinstance(raw, list) or len(raw) > _PAGE_SIZE:
        raise WordPressPostReadUnavailable("WordPress post response invalid")
    posts: list[WordPressPostSummary] = []
    seen: set[int] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise WordPressPostReadUnavailable("WordPress post response invalid")
        post_id = item.get("id")
        status = item.get("status")
        slug = item.get("slug")
        title = item.get("title")
        if (
            type(post_id) is not int
            or post_id < 1
            or post_id in seen
            or not isinstance(status, str)
            or status not in _POST_STATUSES
            or not isinstance(slug, str)
            or len(slug) > 255
            or not isinstance(title, dict)
            or not isinstance(title.get("raw"), str)
            or len(title["raw"]) > 5000
        ):
            raise WordPressPostReadUnavailable("WordPress post response invalid")
        seen.add(post_id)
        posts.append(
            WordPressPostSummary(
                id=post_id, status=status, slug=slug, title=title["raw"]
            )
        )
    return tuple(posts)
