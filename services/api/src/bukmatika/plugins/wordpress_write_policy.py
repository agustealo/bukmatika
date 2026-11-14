from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.plugins.connection_health import ConnectionHealth
from bukmatika.plugins.connections import (
    PluginConnectionRepository,
    PluginConnectionUnavailable,
)
from bukmatika.plugins.contracts import PluginCapability, PluginRegistry
from bukmatika.plugins.execution import PluginExecutionDenied, PluginExecutionPolicy
from bukmatika.plugins.wordpress_capability_service import verify_wordpress_capabilities


class WordPressWriteAction(StrEnum):
    CREATE_DRAFT = "create_draft"
    CREATE_PUBLISHED = "create_published"


class WordPressWriteDenied(PermissionError):
    """A WordPress mutation does not currently have sufficient live authority."""


@dataclass(frozen=True, slots=True)
class WordPressWritePermit:
    """Ephemeral proof for one write action inside the current locked transaction."""

    connection_id: UUID
    action: WordPressWriteAction
    checked_at: datetime
    evidence_code: str
    required_capabilities: tuple[str, ...]


_REQUIRED_CAPABILITIES: dict[WordPressWriteAction, tuple[str, ...]] = {
    WordPressWriteAction.CREATE_DRAFT: ("edit_posts",),
    WordPressWriteAction.CREATE_PUBLISHED: ("edit_posts", "publish_posts"),
}


class WordPressWritePolicy:
    """Compose canonical plugin authority with a fresh live WordPress capability check.

    A permit is intentionally ephemeral. Callers must perform the bounded mutation
    before the surrounding database transaction releases the installation/connection
    row locks. Persisted capability snapshots are never consulted as authorization.
    """

    def __init__(
        self,
        session: AsyncSession,
        registry: PluginRegistry,
        *,
        installation_key: InstallationCredentialKey,
    ) -> None:
        self._session = session
        self._registry = registry
        self._installation_key = installation_key
        self._connections = PluginConnectionRepository(
            session,
            registry,
            installation_key=installation_key,
        )

    async def authorize(
        self,
        *,
        principal_id: UUID,
        action: WordPressWriteAction,
    ) -> WordPressWritePermit:
        try:
            await PluginExecutionPolicy(self._session, self._registry).authorize(
                principal_id=principal_id,
                plugin_id="wordpress",
                capability=PluginCapability.CONTENT_PUBLISH,
                lock=True,
            )
            connection = await self._connections.require_configured(
                principal_id=principal_id,
                plugin_id="wordpress",
            )
        except (PluginExecutionDenied, PluginConnectionUnavailable, LookupError) as exc:
            raise WordPressWriteDenied("WordPress publishing is unavailable") from exc

        observation = await verify_wordpress_capabilities(
            self._session,
            principal_id=principal_id,
            connection_id=connection.id,
            installation_key=self._installation_key,
        )
        required = _REQUIRED_CAPABILITIES[action]
        if (
            observation.state is not ConnectionHealth.VERIFIED
            or not observation.capabilities_observed
            or any(not observation.capabilities.get(capability, False) for capability in required)
        ):
            raise WordPressWriteDenied("Live WordPress capabilities do not permit this action")

        return WordPressWritePermit(
            connection_id=connection.id,
            action=action,
            checked_at=datetime.now(UTC),
            evidence_code=observation.evidence_code,
            required_capabilities=required,
        )
