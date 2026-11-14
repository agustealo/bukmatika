"""Read-only, principal-owned WordPress post listing."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from bukmatika.config import get_settings
from bukmatika.credentials import (
    CredentialIntegrityError,
    CredentialKeyUnavailable,
    CredentialUnavailable,
    InstallationCredentialKey,
)
from bukmatika.identity import AuthenticatedPrincipal, require_principal
from bukmatika.persistence import session_scope
from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.plugin_models import PluginInstallation
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.contracts import PluginCapability
from bukmatika.plugins.execution import PluginExecutionDenied, PluginExecutionPolicy
from bukmatika.plugins.wordpress_capability_probe import probe_wordpress_capabilities
from bukmatika.plugins.wordpress_credentials import load_wordpress_credentials
from bukmatika.plugins.wordpress_posts import (
    WordPressPostReadDenied,
    WordPressPostReadUnavailable,
    WordPressPostSummary,
    fetch_wordpress_posts,
)

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])
_registry = build_builtin_plugin_registry()


class WordPressPost(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    slug: str
    title: str


class WordPressPostPage(BaseModel):
    plugin_id: str = "wordpress"
    scope: str = "authenticated_read_only_posts"
    page: int
    per_page: int = 20
    read_only: bool = True
    publishing_authorized: bool = False
    posts: list[WordPressPost]


@router.get("/{plugin_id}/posts", response_model=WordPressPostPage)
async def list_wordpress_posts(
    plugin_id: str,
    identity: Annotated[AuthenticatedPrincipal, Depends(require_principal)],
    page: Annotated[int, Query(ge=1, le=50)] = 1,
) -> WordPressPostPage:
    """Authorize installation, recheck remote edit scope, then read a bounded page."""
    if plugin_id != "wordpress":
        raise HTTPException(status_code=404, detail={"code": "PLUGIN_NOT_FOUND"})
    async with session_scope() as session:
        try:
            await PluginExecutionPolicy(session, _registry).authorize(
                principal_id=identity.principal_id,
                plugin_id=plugin_id,
                capability=PluginCapability.CONTENT_READ,
            )
        except PluginExecutionDenied as exc:
            raise HTTPException(
                status_code=403, detail={"code": "PLUGIN_READ_NOT_ENABLED"}
            ) from exc
        # Installation and connection row locks fence concurrent disable, rotation,
        # and uninstall for the duration of the two short authenticated reads.
        installation = await session.scalar(
            select(PluginInstallation)
            .where(
                PluginInstallation.principal_id == identity.principal_id,
                PluginInstallation.plugin_id == plugin_id,
            )
            .with_for_update()
        )
        if installation is None or installation.status != "enabled":
            raise HTTPException(
                status_code=403, detail={"code": "PLUGIN_READ_NOT_ENABLED"}
            )
        connection = await session.scalar(
            select(PluginConnection)
            .where(
                PluginConnection.principal_id == identity.principal_id,
                PluginConnection.installation_id == installation.id,
                PluginConnection.plugin_id == plugin_id,
                PluginConnection.mode_id == "rest",
            )
            .with_for_update()
        )
        if connection is None:
            raise HTTPException(
                status_code=409, detail={"code": "CONNECTION_NOT_CONFIGURED"}
            )
        try:
            credentials = await load_wordpress_credentials(
                session,
                principal_id=identity.principal_id,
                connection_id=connection.id,
                installation_key=InstallationCredentialKey(
                    get_settings().credential_key_path
                ),
            )
        except CredentialUnavailable as exc:
            raise HTTPException(
                status_code=409, detail={"code": "CREDENTIAL_UNAVAILABLE"}
            ) from exc
        except (CredentialIntegrityError, CredentialKeyUnavailable) as exc:
            raise HTTPException(
                status_code=503, detail={"code": "CREDENTIAL_VERIFICATION_UNAVAILABLE"}
            ) from exc
        observation = await asyncio.to_thread(
            probe_wordpress_capabilities,
            credentials.site_url,
            credentials.username,
            credentials.application_password,
        )
        if not observation.capabilities_observed:
            raise HTTPException(
                status_code=502, detail={"code": "WORDPRESS_CAPABILITIES_UNAVAILABLE"}
            )
        if not observation.capabilities.get("edit_posts", False):
            raise HTTPException(
                status_code=403, detail={"code": "WORDPRESS_POST_READ_DENIED"}
            )
        try:
            posts: tuple[WordPressPostSummary, ...] = await asyncio.to_thread(
                fetch_wordpress_posts,
                credentials.site_url,
                credentials.username,
                credentials.application_password,
                page=page,
            )
        except WordPressPostReadDenied as exc:
            raise HTTPException(
                status_code=403, detail={"code": "WORDPRESS_POST_READ_DENIED"}
            ) from exc
        except WordPressPostReadUnavailable as exc:
            raise HTTPException(
                status_code=502, detail={"code": "WORDPRESS_POST_READ_UNAVAILABLE"}
            ) from exc
        return WordPressPostPage(
            page=page,
            posts=[WordPressPost.model_validate(post) for post in posts],
        )
