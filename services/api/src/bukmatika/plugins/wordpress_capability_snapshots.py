"""Principal-scoped persistence for WordPress capability observations.

Snapshots are evidence only. They are never sufficient to authorize a write.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.plugin_capability_models import WordPressCapabilitySnapshot
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.wordpress_capability_probe import (
    WORDPRESS_CAPABILITY_EVIDENCE_CODES,
    WORDPRESS_CAPABILITY_KEYS,
    WordPressCapabilityObservation,
)


def snapshot_capabilities(snapshot: WordPressCapabilitySnapshot) -> dict[str, bool]:
    return {
        "edit_posts": snapshot.edit_posts,
        "publish_posts": snapshot.publish_posts,
        "upload_files": snapshot.upload_files,
        "assign_categories": snapshot.assign_categories,
        "manage_categories": snapshot.manage_categories,
        "edit_categories": snapshot.edit_categories,
        "assign_post_tags": snapshot.assign_post_tags,
        "manage_post_tags": snapshot.manage_post_tags,
    }


class WordPressCapabilitySnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
        observation: WordPressCapabilityObservation,
        checked_at: datetime,
    ) -> WordPressCapabilitySnapshot:
        if checked_at.tzinfo is None:
            raise ValueError("Capability snapshot requires an aware timestamp")
        if observation.evidence_code not in WORDPRESS_CAPABILITY_EVIDENCE_CODES:
            raise ValueError("Unrecognized WordPress capability evidence")
        expected_state = (
            ConnectionHealth.VERIFIED
            if observation.evidence_code == "AUTH_CAPABILITIES_200"
            else ConnectionHealth.UNAVAILABLE
        )
        if observation.state is not expected_state:
            raise ValueError("WordPress capability evidence and state disagree")
        if set(observation.capabilities) != set(WORDPRESS_CAPABILITY_KEYS) or any(
            type(value) is not bool for value in observation.capabilities.values()
        ):
            raise ValueError("WordPress capability snapshot has invalid fields")
        if observation.state is ConnectionHealth.UNAVAILABLE and any(
            observation.capabilities.values()
        ):
            raise ValueError("Unavailable capability evidence cannot grant permissions")

        connection = await self._session.scalar(
            select(PluginConnection.id).where(
                PluginConnection.id == connection_id,
                PluginConnection.principal_id == principal_id,
                PluginConnection.plugin_id == "wordpress",
                PluginConnection.mode_id == "rest",
            )
        )
        if connection is None:
            raise LookupError("WordPress REST connection is unavailable")

        values = observation.capabilities
        snapshot = WordPressCapabilitySnapshot(
            principal_id=principal_id,
            connection_id=connection_id,
            state=observation.state.value,
            evidence_code=observation.evidence_code,
            checked_at=checked_at.astimezone(UTC),
            edit_posts=values["edit_posts"],
            publish_posts=values["publish_posts"],
            upload_files=values["upload_files"],
            assign_categories=values["assign_categories"],
            manage_categories=values["manage_categories"],
            edit_categories=values["edit_categories"],
            assign_post_tags=values["assign_post_tags"],
            manage_post_tags=values["manage_post_tags"],
        )
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def latest(
        self,
        *,
        principal_id: UUID,
        connection_id: UUID,
    ) -> WordPressCapabilitySnapshot | None:
        return await self._session.scalar(
            select(WordPressCapabilitySnapshot)
            .where(
                WordPressCapabilitySnapshot.principal_id == principal_id,
                WordPressCapabilitySnapshot.connection_id == connection_id,
            )
            .order_by(
                WordPressCapabilitySnapshot.checked_at.desc(),
                WordPressCapabilitySnapshot.id.desc(),
            )
            .limit(1)
        )
