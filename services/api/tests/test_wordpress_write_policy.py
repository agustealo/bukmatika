from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.plugins.activation import PluginActivationService
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.installations import PluginInstallationRepository
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_KEYS,
    WordPressCapabilityObservation,
)
from bukmatika.plugins.wordpress_capability_snapshots import (
    WordPressCapabilitySnapshotRepository,
)
from bukmatika.plugins.wordpress_write_policy import (
    WordPressWriteAction,
    WordPressWriteDenied,
    WordPressWritePolicy,
)


async def _principal(session: AsyncSession) -> Principal:
    principal = Principal(kind="local", external_subject=f"wp-write-policy-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


def _capabilities(**enabled: bool) -> dict[str, bool]:
    values = {key: False for key in WORDPRESS_CAPABILITY_KEYS}
    values.update(enabled)
    return values


async def _configured_wordpress(
    session: AsyncSession,
    *,
    principal: Principal,
    tmp_path: Path,
) -> tuple[InstallationCredentialKey, object]:
    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=principal.id,
        plugin_id="wordpress",
    )
    key = InstallationCredentialKey(tmp_path / "wp-write-policy.key")
    connection = await PluginConnectionRepository(
        session,
        registry,
        installation_key=key,
    ).configure(
        principal_id=principal.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "publisher",
            "application_password": "application-password",
        },
    )
    return key, connection


async def test_write_policy_denies_disabled_plugin_before_live_probe(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    key, _ = await _configured_wordpress(
        session,
        principal=principal,
        tmp_path=tmp_path,
    )
    registry = build_builtin_plugin_registry()
    policy = WordPressWritePolicy(
        session,
        registry,
        installation_key=key,
    )
    with (
        patch(
            "bukmatika.plugins.wordpress_write_policy.verify_wordpress_capabilities",
            new_callable=AsyncMock,
        ) as verify,
        pytest.raises(WordPressWriteDenied),
    ):
        await policy.authorize(
            principal_id=principal.id,
            action=WordPressWriteAction.CREATE_DRAFT,
        )
    verify.assert_not_awaited()


async def test_write_policy_uses_fresh_capabilities_not_persisted_snapshot(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    key, connection = await _configured_wordpress(
        session,
        principal=principal,
        tmp_path=tmp_path,
    )
    registry = build_builtin_plugin_registry()
    await PluginActivationService(
        session,
        registry,
        installation_key=key,
    ).set_enabled(
        principal_id=principal.id,
        plugin_id="wordpress",
        enabled=True,
    )

    stale = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        _capabilities(edit_posts=True, publish_posts=True),
    )
    await WordPressCapabilitySnapshotRepository(session).record(
        principal_id=principal.id,
        connection_id=connection.id,
        observation=stale,
        checked_at=datetime.now(UTC),
    )

    fresh = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        _capabilities(edit_posts=True, publish_posts=False),
    )
    policy = WordPressWritePolicy(
        session,
        registry,
        installation_key=key,
    )
    with patch(
        "bukmatika.plugins.wordpress_write_policy.verify_wordpress_capabilities",
        new_callable=AsyncMock,
        return_value=fresh,
    ) as verify:
        draft = await policy.authorize(
            principal_id=principal.id,
            action=WordPressWriteAction.CREATE_DRAFT,
        )
        with pytest.raises(WordPressWriteDenied):
            await policy.authorize(
                principal_id=principal.id,
                action=WordPressWriteAction.CREATE_PUBLISHED,
            )

    assert draft.connection_id == connection.id
    assert draft.required_capabilities == ("edit_posts",)
    assert draft.evidence_code == "AUTH_CAPABILITIES_200"
    assert verify.await_count == 2


async def test_write_policy_requires_live_publish_capability(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    principal = await _principal(session)
    key, connection = await _configured_wordpress(
        session,
        principal=principal,
        tmp_path=tmp_path,
    )
    registry = build_builtin_plugin_registry()
    await PluginActivationService(
        session,
        registry,
        installation_key=key,
    ).set_enabled(
        principal_id=principal.id,
        plugin_id="wordpress",
        enabled=True,
    )
    policy = WordPressWritePolicy(
        session,
        registry,
        installation_key=key,
    )
    allowed = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        _capabilities(edit_posts=True, publish_posts=True),
    )
    with patch(
        "bukmatika.plugins.wordpress_write_policy.verify_wordpress_capabilities",
        new_callable=AsyncMock,
        return_value=allowed,
    ):
        permit = await policy.authorize(
            principal_id=principal.id,
            action=WordPressWriteAction.CREATE_PUBLISHED,
        )
    assert permit.connection_id == connection.id
    assert permit.required_capabilities == ("edit_posts", "publish_posts")

    unavailable = WordPressCapabilityObservation(
        ConnectionHealth.UNAVAILABLE,
        "AUTH_CAPABILITY_TRANSPORT_UNAVAILABLE",
        _capabilities(),
    )
    with (
        patch(
            "bukmatika.plugins.wordpress_write_policy.verify_wordpress_capabilities",
            new_callable=AsyncMock,
            return_value=unavailable,
        ),
        pytest.raises(WordPressWriteDenied),
    ):
        await policy.authorize(
            principal_id=principal.id,
            action=WordPressWriteAction.CREATE_DRAFT,
        )
