from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
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
    snapshot_capabilities,
)


def _capabilities(*, edit_posts: bool = False, publish_posts: bool = False) -> dict[str, bool]:
    values = {key: False for key in WORDPRESS_CAPABILITY_KEYS}
    values["edit_posts"] = edit_posts
    values["publish_posts"] = publish_posts
    return values


async def test_capability_snapshots_are_principal_scoped_and_invalidated_on_rotation(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = Principal(kind="local", external_subject=f"wp-snapshot-{uuid4()}")
    outsider = Principal(kind="local", external_subject=f"wp-snapshot-{uuid4()}")
    session.add_all([owner, outsider])
    await session.flush()

    registry = build_builtin_plugin_registry()
    await PluginInstallationRepository(session, registry).install(
        principal_id=owner.id,
        plugin_id="wordpress",
    )
    key = InstallationCredentialKey(tmp_path / "key")
    connections = PluginConnectionRepository(session, registry, installation_key=key)
    connection = await connections.configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "editor",
            "application_password": "first-secret",
        },
    )

    repository = WordPressCapabilitySnapshotRepository(session)
    observation = WordPressCapabilityObservation(
        ConnectionHealth.VERIFIED,
        "AUTH_CAPABILITIES_200",
        _capabilities(edit_posts=True, publish_posts=True),
    )
    checked_at = datetime.now(UTC)
    await repository.record(
        principal_id=owner.id,
        connection_id=connection.id,
        observation=observation,
        checked_at=checked_at,
    )

    stored = await repository.latest(
        principal_id=owner.id,
        connection_id=connection.id,
    )
    assert stored is not None
    assert stored.checked_at == checked_at
    assert snapshot_capabilities(stored) == observation.capabilities
    assert await repository.latest(
        principal_id=outsider.id,
        connection_id=connection.id,
    ) is None
    with pytest.raises(LookupError):
        await repository.record(
            principal_id=outsider.id,
            connection_id=connection.id,
            observation=observation,
            checked_at=checked_at,
        )

    rotated = await connections.configure(
        principal_id=owner.id,
        plugin_id="wordpress",
        mode_id="rest",
        values={
            "site_url": "https://example.com",
            "username": "editor",
            "application_password": "rotated-secret",
        },
    )
    assert rotated.id == connection.id
    assert await repository.latest(
        principal_id=owner.id,
        connection_id=connection.id,
    ) is None

    unavailable = WordPressCapabilityObservation(
        ConnectionHealth.UNAVAILABLE,
        "AUTH_CAPABILITY_REJECTED",
        _capabilities(),
    )
    await repository.record(
        principal_id=owner.id,
        connection_id=connection.id,
        observation=unavailable,
        checked_at=datetime.now(UTC),
    )
    denied = await repository.latest(
        principal_id=owner.id,
        connection_id=connection.id,
    )
    assert denied is not None
    assert denied.state == ConnectionHealth.UNAVAILABLE.value
    assert not any(snapshot_capabilities(denied).values())
