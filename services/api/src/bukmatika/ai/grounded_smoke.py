import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from bukmatika.ai.factory import build_model_gateway
from bukmatika.ai.gateway import (
    ModelGateway,
    ModelProviderNotReady,
    ModelProviderRequestFailed,
    ModelProviderResponseInvalid,
    ModelProviderUnconfigured,
)
from bukmatika.ai.research_service import GroundedResearchSynthesisService
from bukmatika.config import get_settings
from bukmatika.persistence.document_models import Document, DocumentChunk, DocumentSection
from bukmatika.persistence.models import (
    Asset,
    Edition,
    InteractionEvent,
    LibraryEntry,
    Principal,
    StoredObject,
    Work,
)
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import Plan
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.research import (
    ReaderResearchContextRequest,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceReferenceInvalid,
    ResearchService,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

_PROBE_TEXT = (
    "The vessel log records that navigators used Polaris and noon solar observations to estimate "
    "latitude during the Atlantic crossing."
)
_PROBE_QUESTION = "Which observations did the navigators use to estimate latitude?"


class GroundedRuntimeProofFailed(RuntimeError):
    code = "GROUNDED_RUNTIME_PROOF_FAILED"


class GroundedRuntimeProofResult(BaseModel):
    status: Literal["passed"] = "passed"
    provider: str
    model: str
    routing: str
    evidence_count: int
    claim_count: int
    cited_evidence_ids: list[str]
    audit_recorded: bool
    ledger_projected: bool
    rollback_verified: bool


@dataclass(frozen=True, slots=True)
class _ProbeSeed:
    principal_id: UUID
    library_entry: LibraryEntry
    document: Document
    section: DocumentSection


@dataclass(frozen=True, slots=True)
class _ProofExecution:
    principal_id: UUID
    result: GroundedRuntimeProofResult


def _scope(session: AsyncSession) -> SessionScopeFactory:
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


async def _seed_probe(session: AsyncSession) -> _ProbeSeed:
    token = uuid4().hex
    principal = Principal(kind="local", external_subject=f"grounded-runtime-proof:{token}")
    work = Work(
        canonical_title=f"Grounded Runtime Proof {token}",
        normalized_title=f"grounded runtime proof {token}",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/runtime-proof/{token}",
        byte_size=len(_PROBE_TEXT.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([principal, work, stored])
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)

    edition = Edition(
        work_id=work.id,
        title="Grounded Runtime Proof Edition",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.invalid/runtime-proof/{token}.txt",
        stored_object_id=stored.id,
        byte_size=stored.byte_size,
    )
    entry = LibraryEntry(
        principal_id=principal.id,
        work_id=work.id,
        edition_id=edition.id,
        status="saved",
    )
    session.add_all([asset, entry])
    await session.flush()

    document = Document(
        asset_id=asset.id,
        stored_object_id=stored.id,
        source_sha256=stored.sha256,
        format="TXT",
        parser_name="text",
        parser_version="runtime-proof-v1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()

    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading="Navigation observations",
        locator={"page": 1, "section": "navigation-observations"},
        text=_PROBE_TEXT,
    )
    session.add(section)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(_PROBE_TEXT),
            text=_PROBE_TEXT,
        )
    )
    await session.flush()

    return _ProbeSeed(
        principal_id=principal.id,
        library_entry=entry,
        document=document,
        section=section,
    )


def _request(seed: _ProbeSeed) -> ResearchEvidenceBundleRequest:
    return ResearchEvidenceBundleRequest(
        question=_PROBE_QUESTION,
        reader=ReaderResearchContextRequest(
            library_entry_id=seed.library_entry.id,
            document_id=seed.document.id,
            section_id=seed.section.id,
            char_offset=0,
        ),
        library_entry_ids=[seed.library_entry.id],
        related_limit=0,
    )


async def run_grounded_runtime_proof(
    gateway: ModelGateway,
    *,
    session: AsyncSession,
) -> _ProofExecution:
    """Exercise the real grounded synthesis path against rollback-only canonical evidence."""
    seed = await _seed_probe(session)
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )
    response = await service.answer(
        principal_id=seed.principal_id,
        request=_request(seed),
    )

    evidence_ids = {item.evidence_id for item in response.evidence.evidence}
    cited_ids = sorted(
        {
            evidence_id
            for claim in response.answer.claims
            for evidence_id in claim.evidence_ids
        }
    )
    if not evidence_ids or not cited_ids or not set(cited_ids).issubset(evidence_ids):
        raise GroundedRuntimeProofFailed("Grounded answer did not cite canonical probe evidence")

    plan = await session.get(Plan, response.plan_id)
    if plan is None or not plan.steps or plan.steps[0].get("capability") != "research.answer":
        raise GroundedRuntimeProofFailed(
            "Grounded proof did not persist the canonical research plan"
        )
    if _PROBE_TEXT in str(plan.steps) or _PROBE_TEXT in str(plan.context_manifest):
        raise GroundedRuntimeProofFailed(
            "Grounded proof copied raw evidence into persisted plan state"
        )

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == seed.principal_id,
            InteractionEvent.event_type == "ai.model_completed",
            InteractionEvent.entity_id == response.action_decision_id,
        )
    )
    if event is None:
        raise GroundedRuntimeProofFailed("Grounded proof did not record model completion evidence")
    if _PROBE_TEXT in str(event.context) or _PROBE_QUESTION in str(event.context):
        raise GroundedRuntimeProofFailed(
            "Grounded proof leaked probe content into model audit metadata"
        )
    expected_event = {
        "task": "research_answer",
        "provider": response.model_provider,
        "model": response.model_name,
        "routing": response.model_routing,
        "evidence_count": len(response.evidence.evidence),
    }
    if event.context != expected_event:
        raise GroundedRuntimeProofFailed("Grounded proof model audit metadata was incomplete")

    ledger = await PersonalizationControlService(session_scope_factory=scope).activity(
        principal_id=seed.principal_id,
    )
    activity = next(
        (item for item in ledger.items if item.decision_id == response.action_decision_id),
        None,
    )
    if activity is None:
        raise GroundedRuntimeProofFailed(
            "Grounded proof action was absent from the AI activity ledger"
        )
    if (
        activity.model_provider != response.model_provider
        or activity.model_name != response.model_name
    ):
        raise GroundedRuntimeProofFailed("Grounded proof model identity was absent from the ledger")

    return _ProofExecution(
        principal_id=seed.principal_id,
        result=GroundedRuntimeProofResult(
            provider=response.model_provider,
            model=response.model_name,
            routing=response.model_routing,
            evidence_count=len(response.evidence.evidence),
            claim_count=len(response.answer.claims),
            cited_evidence_ids=cited_ids,
            audit_recorded=True,
            ledger_projected=True,
            rollback_verified=False,
        ),
    )


async def _verify_rollback(engine: AsyncEngine, principal_id: UUID) -> None:
    async with AsyncSession(bind=engine, expire_on_commit=False) as session:
        persisted = await session.get(Principal, principal_id)
    if persisted is not None:
        raise GroundedRuntimeProofFailed("Grounded runtime proof left probe data in the database")


async def _run_from_settings() -> GroundedRuntimeProofResult:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    execution: _ProofExecution | None = None

    try:
        async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
            gateway = build_model_gateway(settings=settings, client=client)
            async with engine.connect() as connection:
                transaction = await connection.begin()
                session = AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    autoflush=False,
                )
                try:
                    execution = await run_grounded_runtime_proof(gateway, session=session)
                finally:
                    await session.close()
                    if transaction.is_active:
                        await transaction.rollback()

        if execution is None:
            raise GroundedRuntimeProofFailed("Grounded runtime proof did not execute")
        await _verify_rollback(engine, execution.principal_id)
        return execution.result.model_copy(update={"rollback_verified": True})
    except SQLAlchemyError as exc:
        raise GroundedRuntimeProofFailed("Grounded runtime database proof failed") from exc
    finally:
        await engine.dispose()


def _failure_payload(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ModelProviderNotReady):
        return {
            "status": "failed",
            "code": exc.code,
            "state": exc.readiness.state.value,
        }
    return {
        "status": "failed",
        "code": str(getattr(exc, "code", type(exc).__name__)),
    }


def main() -> None:
    try:
        result = asyncio.run(_run_from_settings())
    except (
        GroundedRuntimeProofFailed,
        ModelProviderUnconfigured,
        ModelProviderNotReady,
        ModelProviderRequestFailed,
        ModelProviderResponseInvalid,
        ResearchEvidenceReferenceInvalid,
    ) as exc:
        print(json.dumps(_failure_payload(exc), sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None

    print(result.model_dump_json())
