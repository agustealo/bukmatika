import hashlib
import json
import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.plugin_connection_models import PluginConnection
from bukmatika.persistence.publishing_models import (
    Publication,
    PublicationDelivery,
    PublicationDeliveryAttempt,
    PublicationDestination,
    PublicationRevision,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PublicationNotFound(LookupError):
    pass


class PublicationStateConflict(RuntimeError):
    pass


class PublicationDeliveryIdempotencyConflict(RuntimeError):
    pass


class PublicationDeliveryInFlight(RuntimeError):
    pass


class PublicationDeliveryAlreadySucceeded(RuntimeError):
    pass


def publication_content_sha256(*, title: str, body: str, excerpt: str | None) -> str:
    payload = json.dumps(
        {"title": title, "body": body, "excerpt": excerpt},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_text(value: str, *, field: str, max_length: int | None = None) -> str:
    if not value.strip():
        raise ValueError(f"{field} must not be blank")
    if max_length is not None and len(value) > max_length:
        raise ValueError(f"{field} is too long")
    return value


def _require_sha256(value: str, *, field: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


class PublicationRepository:
    """Canonical publication/revision/destination/delivery persistence authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        principal_id: UUID,
        title: str,
        body: str,
        excerpt: str | None = None,
    ) -> tuple[Publication, PublicationRevision]:
        _require_text(title, field="title")
        publication = Publication(principal_id=principal_id, state="active")
        self._session.add(publication)
        await self._session.flush()
        revision = PublicationRevision(
            principal_id=principal_id,
            publication_id=publication.id,
            revision_number=1,
            title=title,
            body=body,
            excerpt=excerpt,
            content_sha256=publication_content_sha256(
                title=title,
                body=body,
                excerpt=excerpt,
            ),
        )
        self._session.add(revision)
        await self._session.flush()
        return publication, revision

    async def revise(
        self,
        *,
        principal_id: UUID,
        publication_id: UUID,
        title: str,
        body: str,
        excerpt: str | None = None,
    ) -> PublicationRevision:
        _require_text(title, field="title")
        publication = await self._require_publication(
            principal_id=principal_id,
            publication_id=publication_id,
            lock=True,
        )
        if publication.state != "active":
            raise PublicationStateConflict("Trashed publications cannot be revised")
        latest = await self._session.scalar(
            select(func.max(PublicationRevision.revision_number)).where(
                PublicationRevision.principal_id == principal_id,
                PublicationRevision.publication_id == publication_id,
            )
        )
        revision = PublicationRevision(
            principal_id=principal_id,
            publication_id=publication_id,
            revision_number=(latest or 0) + 1,
            title=title,
            body=body,
            excerpt=excerpt,
            content_sha256=publication_content_sha256(
                title=title,
                body=body,
                excerpt=excerpt,
            ),
        )
        self._session.add(revision)
        await self._session.flush()
        return revision

    async def ensure_destination(
        self,
        *,
        principal_id: UUID,
        plugin_id: str,
        connection_id: UUID,
        destination_kind: str,
        external_locator: str,
    ) -> PublicationDestination:
        _require_text(plugin_id, field="plugin_id", max_length=128)
        _require_text(destination_kind, field="destination_kind", max_length=64)
        _require_text(external_locator, field="external_locator")
        connection = await self._session.scalar(
            select(PluginConnection).where(
                PluginConnection.id == connection_id,
                PluginConnection.principal_id == principal_id,
                PluginConnection.plugin_id == plugin_id,
            )
        )
        if connection is None:
            raise PublicationNotFound("Plugin connection is unavailable")

        destination_id = await self._session.scalar(
            insert(PublicationDestination)
            .values(
                id=uuid4(),
                principal_id=principal_id,
                plugin_id=plugin_id,
                connection_id=connection_id,
                destination_kind=destination_kind,
                external_locator=external_locator,
                state="active",
            )
            .on_conflict_do_update(
                index_elements=[
                    "principal_id",
                    "plugin_id",
                    "destination_kind",
                    "external_locator",
                ],
                set_={
                    "connection_id": connection_id,
                    "state": "active",
                    "updated_at": func.now(),
                },
            )
            .returning(PublicationDestination.id)
        )
        if destination_id is None:
            raise RuntimeError("Publication destination upsert did not return an identity")
        destination = await self._session.scalar(
            select(PublicationDestination)
            .where(
                PublicationDestination.id == destination_id,
                PublicationDestination.principal_id == principal_id,
            )
            .execution_options(populate_existing=True)
        )
        if destination is None:
            raise RuntimeError("Publication destination upsert could not be resolved")
        return destination

    async def create_delivery(
        self,
        *,
        principal_id: UUID,
        publication_id: UUID,
        revision_id: UUID,
        destination_id: UUID,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> PublicationDelivery:
        _require_text(idempotency_key, field="idempotency_key", max_length=128)
        _require_sha256(request_fingerprint, field="request_fingerprint")
        await self._require_delivery_inputs(
            principal_id=principal_id,
            publication_id=publication_id,
            revision_id=revision_id,
            destination_id=destination_id,
        )

        delivery_id = uuid4()
        inserted_id = await self._session.scalar(
            insert(PublicationDelivery)
            .values(
                id=delivery_id,
                principal_id=principal_id,
                publication_id=publication_id,
                revision_id=revision_id,
                destination_id=destination_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                state="pending",
                latest_attempt=0,
            )
            .on_conflict_do_nothing(
                index_elements=["principal_id", "destination_id", "idempotency_key"]
            )
            .returning(PublicationDelivery.id)
        )
        if inserted_id is not None:
            delivery = await self._session.get(PublicationDelivery, inserted_id)
            assert delivery is not None
            return delivery

        existing = await self._session.scalar(
            select(PublicationDelivery).where(
                PublicationDelivery.principal_id == principal_id,
                PublicationDelivery.destination_id == destination_id,
                PublicationDelivery.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise RuntimeError("Delivery idempotency conflict could not be resolved")
        if (
            existing.publication_id != publication_id
            or existing.revision_id != revision_id
            or existing.request_fingerprint != request_fingerprint
        ):
            raise PublicationDeliveryIdempotencyConflict(
                "Idempotency key was already used for a different delivery"
            )
        return existing

    async def begin_attempt(
        self,
        *,
        principal_id: UUID,
        delivery_id: UUID,
    ) -> PublicationDeliveryAttempt:
        delivery = await self._require_delivery(
            principal_id=principal_id,
            delivery_id=delivery_id,
            lock=True,
        )
        if delivery.state == "succeeded":
            raise PublicationDeliveryAlreadySucceeded("Delivery is already settled")
        started = await self._session.scalar(
            select(PublicationDeliveryAttempt).where(
                PublicationDeliveryAttempt.principal_id == principal_id,
                PublicationDeliveryAttempt.delivery_id == delivery_id,
                PublicationDeliveryAttempt.state == "started",
            )
        )
        if started is not None:
            raise PublicationDeliveryInFlight("Delivery already has an in-flight attempt")

        attempt_number = delivery.latest_attempt + 1
        attempt = PublicationDeliveryAttempt(
            principal_id=principal_id,
            delivery_id=delivery_id,
            attempt_number=attempt_number,
            state="started",
        )
        self._session.add(attempt)
        delivery.latest_attempt = attempt_number
        delivery.state = "in_flight"
        delivery.last_error_code = None
        await self._session.flush()
        return attempt

    async def finish_attempt(
        self,
        *,
        principal_id: UUID,
        delivery_id: UUID,
        attempt_number: int,
        outcome: str,
        evidence_code: str,
        response_fingerprint: str | None = None,
        remote_resource_id: str | None = None,
        remote_revision_id: str | None = None,
        remote_url: str | None = None,
        remote_status: str | None = None,
        error_code: str | None = None,
    ) -> PublicationDelivery:
        if outcome not in {"succeeded", "failed", "unknown"}:
            raise ValueError("Unsupported delivery outcome")
        _require_text(evidence_code, field="evidence_code", max_length=64)
        if response_fingerprint is not None:
            _require_sha256(response_fingerprint, field="response_fingerprint")
        if outcome == "succeeded":
            if remote_resource_id is None or not remote_resource_id.strip():
                raise ValueError("Successful delivery requires a remote resource identifier")
            if error_code is not None:
                raise ValueError("Successful delivery cannot include an error code")
        else:
            if error_code is None or not error_code.strip():
                raise ValueError("Non-successful delivery requires an error code")
            _require_text(error_code, field="error_code", max_length=64)

        delivery = await self._require_delivery(
            principal_id=principal_id,
            delivery_id=delivery_id,
            lock=True,
        )
        attempt = await self._session.scalar(
            select(PublicationDeliveryAttempt)
            .where(
                PublicationDeliveryAttempt.principal_id == principal_id,
                PublicationDeliveryAttempt.delivery_id == delivery_id,
                PublicationDeliveryAttempt.attempt_number == attempt_number,
            )
            .with_for_update()
        )
        if attempt is None or attempt.state != "started":
            raise PublicationStateConflict("Delivery attempt is not active")
        if delivery.latest_attempt != attempt_number or delivery.state != "in_flight":
            raise PublicationStateConflict("Delivery state does not match the active attempt")

        completed_at = datetime.now(UTC)
        attempt.state = outcome
        attempt.evidence_code = evidence_code
        attempt.response_fingerprint = response_fingerprint
        attempt.remote_resource_id = remote_resource_id
        attempt.remote_revision_id = remote_revision_id
        attempt.remote_url = remote_url
        attempt.remote_status = remote_status
        attempt.error_code = error_code
        attempt.completed_at = completed_at

        delivery.state = outcome
        delivery.remote_resource_id = remote_resource_id
        delivery.remote_revision_id = remote_revision_id
        delivery.remote_url = remote_url
        delivery.remote_status = remote_status
        delivery.last_error_code = error_code
        delivery.settled_at = completed_at if outcome == "succeeded" else None
        await self._session.flush()
        return delivery

    async def _require_publication(
        self,
        *,
        principal_id: UUID,
        publication_id: UUID,
        lock: bool = False,
    ) -> Publication:
        query = select(Publication).where(
            Publication.id == publication_id,
            Publication.principal_id == principal_id,
        )
        if lock:
            query = query.with_for_update()
        publication = await self._session.scalar(query)
        if publication is None:
            raise PublicationNotFound("Publication is unavailable")
        return publication

    async def _require_delivery_inputs(
        self,
        *,
        principal_id: UUID,
        publication_id: UUID,
        revision_id: UUID,
        destination_id: UUID,
    ) -> None:
        publication = await self._require_publication(
            principal_id=principal_id,
            publication_id=publication_id,
        )
        if publication.state != "active":
            raise PublicationStateConflict("Trashed publications cannot be delivered")
        revision = await self._session.scalar(
            select(PublicationRevision.id).where(
                PublicationRevision.id == revision_id,
                PublicationRevision.publication_id == publication_id,
                PublicationRevision.principal_id == principal_id,
            )
        )
        if revision is None:
            raise PublicationNotFound("Publication revision is unavailable")
        destination = await self._session.scalar(
            select(PublicationDestination).where(
                PublicationDestination.id == destination_id,
                PublicationDestination.principal_id == principal_id,
                PublicationDestination.state == "active",
            )
        )
        if destination is None:
            raise PublicationNotFound("Publication destination is unavailable")

    async def _require_delivery(
        self,
        *,
        principal_id: UUID,
        delivery_id: UUID,
        lock: bool = False,
    ) -> PublicationDelivery:
        query = select(PublicationDelivery).where(
            PublicationDelivery.id == delivery_id,
            PublicationDelivery.principal_id == principal_id,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        delivery = await self._session.scalar(query)
        if delivery is None:
            raise PublicationNotFound("Publication delivery is unavailable")
        return delivery
