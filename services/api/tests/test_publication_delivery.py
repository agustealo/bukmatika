from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.credentials import InstallationCredentialKey
from bukmatika.persistence.models import Principal
from bukmatika.plugins.builtin import build_builtin_plugin_registry
from bukmatika.plugins.connections import PluginConnectionRepository
from bukmatika.plugins.installations import PluginInstallationRepository
from bukmatika.publishing import (
    PublicationDeliveryAlreadySucceeded,
    PublicationDeliveryIdempotencyConflict,
    PublicationDeliveryInFlight,
    PublicationNotFound,
    PublicationRepository,
    publication_content_sha256,
)


async def _principal(session: AsyncSession, label: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"publishing-{label}-{uuid4()}")
    session.add(principal)
    await session.flush()
    return principal


async def _wordpress_connection(
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
    key = InstallationCredentialKey(tmp_path / f"{principal.id}.key")
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
            "username": "editor",
            "application_password": "private-application-password",
        },
    )
    return key, connection


async def test_publication_revisions_are_immutable_and_principal_scoped(
    session: AsyncSession,
) -> None:
    owner = await _principal(session, "owner")
    outsider = await _principal(session, "outsider")
    repository = PublicationRepository(session)

    publication, first = await repository.create(
        principal_id=owner.id,
        title="First title",
        body="First body",
        excerpt="First excerpt",
    )
    second = await repository.revise(
        principal_id=owner.id,
        publication_id=publication.id,
        title="Second title",
        body="Second body",
        excerpt=None,
    )

    assert first.revision_number == 1
    assert first.title == "First title"
    assert first.body == "First body"
    assert first.content_sha256 == publication_content_sha256(
        title="First title",
        body="First body",
        excerpt="First excerpt",
    )
    assert second.revision_number == 2
    assert second.title == "Second title"
    assert second.content_sha256 != first.content_sha256

    with pytest.raises(PublicationNotFound):
        await repository.revise(
            principal_id=outsider.id,
            publication_id=publication.id,
            title="Cross-owner write",
            body="Denied",
        )


async def test_delivery_idempotency_attempt_fencing_and_historical_destination(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = await _principal(session, "owner")
    outsider = await _principal(session, "outsider")
    key, connection = await _wordpress_connection(
        session,
        principal=owner,
        tmp_path=tmp_path,
    )
    registry = build_builtin_plugin_registry()
    repository = PublicationRepository(session)
    publication, revision = await repository.create(
        principal_id=owner.id,
        title="Publish me",
        body="Durable body",
    )
    destination = await repository.ensure_destination(
        principal_id=owner.id,
        plugin_id="wordpress",
        connection_id=connection.id,
        destination_kind="post",
        external_locator="https://example.com",
    )
    request_fingerprint = revision.content_sha256
    delivery = await repository.create_delivery(
        principal_id=owner.id,
        publication_id=publication.id,
        revision_id=revision.id,
        destination_id=destination.id,
        idempotency_key="client-request-1",
        request_fingerprint=request_fingerprint,
    )
    replay = await repository.create_delivery(
        principal_id=owner.id,
        publication_id=publication.id,
        revision_id=revision.id,
        destination_id=destination.id,
        idempotency_key="client-request-1",
        request_fingerprint=request_fingerprint,
    )
    assert replay.id == delivery.id

    with pytest.raises(PublicationDeliveryIdempotencyConflict):
        await repository.create_delivery(
            principal_id=owner.id,
            publication_id=publication.id,
            revision_id=revision.id,
            destination_id=destination.id,
            idempotency_key="client-request-1",
            request_fingerprint="0" * 64,
        )
    with pytest.raises(PublicationNotFound):
        await repository.create_delivery(
            principal_id=outsider.id,
            publication_id=publication.id,
            revision_id=revision.id,
            destination_id=destination.id,
            idempotency_key="cross-owner",
            request_fingerprint=request_fingerprint,
        )

    first_attempt = await repository.begin_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
    )
    assert first_attempt.attempt_number == 1
    assert delivery.state == "in_flight"
    with pytest.raises(PublicationDeliveryInFlight):
        await repository.begin_attempt(
            principal_id=owner.id,
            delivery_id=delivery.id,
        )

    failed = await repository.finish_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
        attempt_number=1,
        outcome="failed",
        evidence_code="WORDPRESS_HTTP_503",
        error_code="REMOTE_UNAVAILABLE",
    )
    assert failed.state == "failed"
    assert failed.latest_attempt == 1
    assert failed.last_error_code == "REMOTE_UNAVAILABLE"

    second_attempt = await repository.begin_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
    )
    assert second_attempt.attempt_number == 2
    succeeded = await repository.finish_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
        attempt_number=2,
        outcome="succeeded",
        evidence_code="WORDPRESS_POST_CREATED",
        response_fingerprint="1" * 64,
        remote_resource_id="42",
        remote_revision_id="7",
        remote_url="https://example.com/?p=42",
        remote_status="draft",
    )
    assert succeeded.state == "succeeded"
    assert succeeded.remote_resource_id == "42"
    assert succeeded.remote_revision_id == "7"
    assert succeeded.settled_at is not None
    with pytest.raises(PublicationDeliveryAlreadySucceeded):
        await repository.begin_attempt(
            principal_id=owner.id,
            delivery_id=delivery.id,
        )

    await PluginInstallationRepository(session, registry).uninstall(
        principal_id=owner.id,
        plugin_id="wordpress",
    )
    await session.refresh(destination)
    assert destination.connection_id is None
    assert destination.external_locator == "https://example.com"
    assert destination.plugin_id == "wordpress"

    # The credential key exists only to configure the source and must not be retained
    # by publication history.
    assert key.path.exists()


async def test_unknown_attempt_blocks_blind_replay_until_explicit_retry(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner = await _principal(session, "unknown")
    _, connection = await _wordpress_connection(
        session,
        principal=owner,
        tmp_path=tmp_path,
    )
    repository = PublicationRepository(session)
    publication, revision = await repository.create(
        principal_id=owner.id,
        title="Ambiguous outcome",
        body="Network disconnected after send.",
    )
    destination = await repository.ensure_destination(
        principal_id=owner.id,
        plugin_id="wordpress",
        connection_id=connection.id,
        destination_kind="post",
        external_locator="https://example.com",
    )
    delivery = await repository.create_delivery(
        principal_id=owner.id,
        publication_id=publication.id,
        revision_id=revision.id,
        destination_id=destination.id,
        idempotency_key="ambiguous-send",
        request_fingerprint=revision.content_sha256,
    )
    await repository.begin_attempt(principal_id=owner.id, delivery_id=delivery.id)
    unknown = await repository.finish_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
        attempt_number=1,
        outcome="unknown",
        evidence_code="WORDPRESS_RESPONSE_LOST",
        error_code="REMOTE_OUTCOME_UNKNOWN",
    )
    assert unknown.state == "unknown"
    assert unknown.last_error_code == "REMOTE_OUTCOME_UNKNOWN"

    retry = await repository.begin_attempt(
        principal_id=owner.id,
        delivery_id=delivery.id,
    )
    assert retry.attempt_number == 2
