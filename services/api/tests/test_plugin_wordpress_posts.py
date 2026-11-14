"""Adversarial tests for WordPress authenticated read-only post listing."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.execution import PluginExecutionDenied
from bukmatika.plugins.pinned_https import WordPressHttpResponse, get_wordpress_rest
from bukmatika.plugins.transport_policy import ApprovedHttpsDestination
from bukmatika.plugins.wordpress_capability_probe import WordPressCapabilityObservation
from bukmatika.plugins.wordpress_post_routes import list_wordpress_posts, router
from bukmatika.plugins.wordpress_posts import (
    WordPressPostReadDenied,
    WordPressPostReadUnavailable,
    WordPressPostSummary,
    fetch_wordpress_posts,
)


def test_wordpress_posts_are_bounded_scoped_and_never_echo_credentials() -> None:
    data = [{"id": 9, "status": "draft", "slug": "my-draft",
             "title": {"raw": "Unpublished title"}, "content": {"raw": "sensitive"}}]
    with patch(
        "bukmatika.plugins.wordpress_posts.get_wordpress_rest",
        return_value=WordPressHttpResponse(200, json.dumps(data).encode()),
    ) as request:
        result = fetch_wordpress_posts("https://example.com", "editor", "private", page=1)
    assert result == (
        WordPressPostSummary(id=9, status="draft", slug="my-draft", title="Unpublished title"),
    )
    assert request.call_args.args[1] == (
        "/wp-json/wp/v2/posts?context=edit&per_page=20&page=1"
        "&_fields=id,status,slug,title"
    )
    assert request.call_args.kwargs["max_bytes"] == 262144
    assert request.call_args.kwargs["authorization"].startswith("Basic ")
    assert "private" not in repr(result)
    assert "sensitive" not in repr(result)


@pytest.mark.parametrize("code", [401, 403])
def test_wp_denial_is_not_success(code: int) -> None:
    with patch(
        "bukmatika.plugins.wordpress_posts.get_wordpress_rest",
        return_value=WordPressHttpResponse(code, b"{}"),
    ), pytest.raises(WordPressPostReadDenied):
        fetch_wordpress_posts("https://example.com", "editor", "secret", page=1)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json", b"{}", b'[{"id":9}]',
        json.dumps([
            {"id": 1, "status": "draft", "slug": "a", "title": {"raw": "a"}},
            {"id": 1, "status": "draft", "slug": "b", "title": {"raw": "b"}},
        ]).encode(),
        json.dumps([
            {"id": True, "status": "draft", "slug": "a", "title": {"raw": "a"}},
        ]).encode(),
    ],
)
def test_malformed_remote_post_data_fails_closed(body: bytes) -> None:
    with patch(
        "bukmatika.plugins.wordpress_posts.get_wordpress_rest",
        return_value=WordPressHttpResponse(200, body),
    ), pytest.raises(WordPressPostReadUnavailable):
        fetch_wordpress_posts("https://example.com", "editor", "secret", page=1)


def test_invalid_page_and_network_errors_never_leak_password() -> None:
    with patch("bukmatika.plugins.wordpress_posts.get_wordpress_rest") as request:
        for page in (0, 51, True):
            with pytest.raises(WordPressPostReadUnavailable):
                fetch_wordpress_posts("https://example.com", "editor", "secret", page=page)
        request.assert_not_called()
    with patch(
        "bukmatika.plugins.wordpress_posts.get_wordpress_rest",
        side_effect=OSError("private credential"),
    ), pytest.raises(WordPressPostReadUnavailable) as exc:
        fetch_wordpress_posts("https://example.com", "editor", "private", page=1)
    assert "private" not in str(exc.value)


def test_exact_authenticated_transport_route_and_rejected_query_variants() -> None:
    dest = ApprovedHttpsDestination(
        origin="https://example.com", hostname="example.com", addresses=("8.8.8.8",)
    )
    route = (
        "/wp-json/wp/v2/posts?context=edit&per_page=20&page=1"
        "&_fields=id,status,slug,title"
    )
    with (
        patch("bukmatika.plugins.pinned_https.approve_https_destination", return_value=dest),
        patch("bukmatika.plugins.pinned_https.PinnedHttpsConnection") as connection,
    ):
        connection.return_value.getresponse.return_value = MagicMock(
            status=200, read=MagicMock(return_value=b"[]")
        )
        assert get_wordpress_rest(
            "https://example.com", route, authorization="Basic c2FmZQ=="
        ).status_code == 200
    assert connection.return_value.request.call_args.args[:2] == ("GET", route)
    connection.return_value.close.assert_called_once()
    for other in (
        "/wp-json/wp/v2/posts?context=edit",
        route.replace("page=1", "page=51"),
        route + "&inject=1",
    ):
        with pytest.raises(ValueError):
            get_wordpress_rest(
                "https://example.com", other, authorization="Basic c2FmZQ=="
            )


def test_read_route_is_authenticated_and_does_not_enable_publish() -> None:
    matches = [
        item for item in router.routes
        if isinstance(item, APIRoute)
        and item.path == "/v1/plugins/{plugin_id}/posts"
    ]
    assert len(matches) == 1
    assert matches[0].methods == {"GET"}
    assert matches[0].dependant.dependencies
    from bukmatika.plugins.wordpress_post_routes import WordPressPostPage

    assert WordPressPostPage.model_fields["publishing_authorized"].default is False
    assert WordPressPostPage.model_fields["read_only"].default is True



@asynccontextmanager
async def _fake_session():
    yield MagicMock()


@pytest.mark.asyncio
async def test_disabled_plugin_fails_before_network() -> None:
    with (
        patch("bukmatika.plugins.wordpress_post_routes.session_scope", _fake_session),
        patch("bukmatika.plugins.wordpress_post_routes.PluginExecutionPolicy") as policy,
        patch(
            "bukmatika.plugins.wordpress_post_routes.asyncio.to_thread",
            new_callable=AsyncMock,
        ) as network,
        pytest.raises(HTTPException) as exc,
    ):
        policy.return_value.authorize = AsyncMock(
            side_effect=PluginExecutionDenied("disabled")
        )
        await list_wordpress_posts(
            "wordpress", SimpleNamespace(principal_id=uuid4())
        )
    assert exc.value.status_code == 403
    network.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_edit_permission_denial_prevents_post_fetch() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(
        side_effect=[
            SimpleNamespace(id=uuid4(), status="enabled"),
            SimpleNamespace(id=uuid4()),
        ]
    )
    @asynccontextmanager
    async def session_scope():
        yield session
    with (
        patch("bukmatika.plugins.wordpress_post_routes.session_scope", session_scope),
        patch("bukmatika.plugins.wordpress_post_routes.PluginExecutionPolicy") as policy,
        patch(
            "bukmatika.plugins.wordpress_post_routes.load_wordpress_credentials",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                site_url="https://example.com",
                username="editor",
                application_password="private",
            ),
        ),
        patch("bukmatika.plugins.wordpress_post_routes.get_settings") as settings,
        patch(
            "bukmatika.plugins.wordpress_post_routes.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=WordPressCapabilityObservation(
                ConnectionHealth.VERIFIED,
                "AUTH_CAPABILITIES_200",
                {"edit_posts": False},
            ),
        ) as network,
        pytest.raises(HTTPException) as exc,
    ):
        policy.return_value.authorize = AsyncMock()
        settings.return_value.credential_key_path = "/tmp/test-only-key"
        await list_wordpress_posts(
            "wordpress", SimpleNamespace(principal_id=uuid4())
        )
    assert exc.value.status_code == 403
    network.assert_awaited_once()
