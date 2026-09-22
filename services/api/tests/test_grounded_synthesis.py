from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelProviderIdentity,
    ModelProviderNotReady,
    ModelProviderReadiness,
    ModelProviderUnconfigured,
    ModelReadinessState,
    ModelRequest,
    ModelTask,
    UnconfiguredModelGateway,
)
from bukmatika.ai.research_service import GroundedResearchSynthesisService
from bukmatika.ai.service import AIDisabled
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
from bukmatika.persistence.personalization_models import Plan, UserModel
from bukmatika.personalization.control import PersonalizationControlService
from bukmatika.research import (
    ReaderResearchContextRequest,
    ResearchEvidenceBundleRequest,
    ResearchEvidenceReferenceInvalid,
    ResearchService,
)


def _scope(session: AsyncSession):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def scope() -> AsyncIterator[AsyncSession]:
        yield session

    return scope


class _RecordingGateway:
    def __init__(
        self,
        evidence_id: str = "E1",
        *,
        readiness_state: ModelReadinessState = ModelReadinessState.READY,
    ) -> None:
        self._identity = ModelProviderIdentity(
            provider="ollama",
            model="qwen3:8b",
            routing="local",
        )
        self._evidence_id = evidence_id
        self._readiness_state = readiness_state
        self.calls: list[ModelRequest] = []
        self.readiness_calls = 0

    @property
    def identity(self) -> ModelProviderIdentity:
        return self._identity

    async def readiness(self) -> ModelProviderReadiness:
        self.readiness_calls += 1
        return ModelProviderReadiness(
            state=self._readiness_state,
            configured=True,
            ready=self._readiness_state is ModelReadinessState.READY,
            identity=self._identity,
        )

    async def generate_structured(self, request, response_type):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        return response_type.model_validate(
            {
                "claims": [
                    {
                        "text": "The passage describes documented Atlantic navigation evidence.",
                        "evidence_ids": [self._evidence_id],
                    }
                ]
            }
        )


async def _principal(session: AsyncSession, suffix: str) -> Principal:
    principal = Principal(kind="local", external_subject=f"synthesis-{suffix}")
    session.add(principal)
    await session.flush()
    await PersonalizationRepository(session).get_or_create_user_model(principal.id)
    return principal


async def _seed_book(
    session: AsyncSession,
    *,
    principal: Principal,
    suffix: str,
    text: str,
) -> tuple[LibraryEntry, Document, DocumentSection]:
    token = uuid4().hex
    work = Work(
        canonical_title=f"Synthesis Work {suffix}",
        normalized_title=f"synthesis work {suffix}",
    )
    stored = StoredObject(
        sha256=token + token,
        storage_key=f"objects/synthesis/{suffix}/{token}",
        byte_size=len(text.encode("utf-8")),
        media_type="text/plain",
    )
    session.add_all([work, stored])
    await session.flush()

    edition = Edition(
        work_id=work.id,
        title=f"Synthesis Edition {suffix}",
        language="en",
        publication_year=1900,
    )
    session.add(edition)
    await session.flush()

    asset = Asset(
        edition_id=edition.id,
        format="TXT",
        media_type="text/plain",
        remote_url=f"https://example.org/synthesis/{suffix}.txt",
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
        parser_version="1",
        section_count=1,
        chunk_count=1,
    )
    session.add(document)
    await session.flush()

    section = DocumentSection(
        document_id=document.id,
        ordinal=0,
        heading=f"Section {suffix}",
        locator={"page": 1, "section": suffix},
        text=text,
    )
    session.add(section)
    await session.flush()
    session.add(
        DocumentChunk(
            document_id=document.id,
            section_id=section.id,
            ordinal=0,
            char_start=0,
            char_end=len(text),
            text=text,
        )
    )
    await session.flush()
    return entry, document, section


def _request(
    entry: LibraryEntry,
    document: Document,
    section: DocumentSection,
) -> ResearchEvidenceBundleRequest:
    return ResearchEvidenceBundleRequest(
        question="What navigation evidence does this passage provide?",
        reader=ReaderResearchContextRequest(
            library_entry_id=entry.id,
            document_id=document.id,
            section_id=section.id,
            char_offset=0,
        ),
        library_entry_ids=[entry.id],
        related_limit=0,
    )


async def test_grounded_synthesis_uses_only_canonical_bundle_and_is_auditable(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "success")
    text = "Documented Atlantic navigation relied on routes, observations, and recorded bearings."
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="success",
        text=text,
    )
    gateway = _RecordingGateway()
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    result = await service.answer(
        principal_id=principal.id,
        request=_request(entry, document, section),
    )

    assert gateway.readiness_calls == 1
    assert result.model_provider == "ollama"
    assert result.model_name == "qwen3:8b"
    assert result.model_routing == "local"
    assert result.answer.claims[0].evidence_ids == ["E1"]
    assert result.evidence.evidence[0].evidence_id == "E1"
    assert len(gateway.calls) == 1

    model_request = gateway.calls[0]
    assert model_request.task is ModelTask.RESEARCH_ANSWER
    assert model_request.data_classification is ModelDataClassification.PRIVATE_USER_CONTEXT
    assert set(model_request.payload) == {"question", "evidence"}
    model_evidence = model_request.payload["evidence"]
    assert isinstance(model_evidence, list)
    assert len(model_evidence) == 1
    item = model_evidence[0]
    assert isinstance(item, dict)
    assert item["evidence_id"] == "E1"
    assert item["text"] == text
    assert "library_entry_id" not in item
    assert "chunk_id" not in item
    assert "principal_id" not in item

    plan = await session.get(Plan, result.plan_id)
    assert plan is not None
    assert plan.steps[0]["capability"] == "research.answer"
    assert "research.answer" in plan.context_manifest["available_capabilities"]
    assert text not in str(plan.steps)
    assert text not in str(plan.context_manifest)

    event = await session.scalar(
        select(InteractionEvent).where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "ai.model_completed",
            InteractionEvent.entity_id == result.action_decision_id,
        )
    )
    assert event is not None
    assert event.entity_type == "action_decision"
    assert event.context == {
        "task": "research_answer",
        "provider": "ollama",
        "model": "qwen3:8b",
        "routing": "local",
        "evidence_count": 1,
    }
    assert text not in str(event.context)
    assert result.evidence.question not in str(event.context)

    ledger = await PersonalizationControlService(session_scope_factory=scope).activity(
        principal_id=principal.id,
    )
    activity = next(item for item in ledger.items if item.decision_id == result.action_decision_id)
    assert activity.model_provider == "ollama"
    assert activity.model_name == "qwen3:8b"


async def test_ai_off_rejects_before_readiness_or_model_call_but_evidence_still_works(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "ai-off")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="ai-off",
        text="A canonical passage remains searchable when AI is disabled.",
    )
    model = await session.scalar(select(UserModel).where(UserModel.principal_id == principal.id))
    assert model is not None
    model.ai_enabled = False
    await session.flush()

    gateway = _RecordingGateway()
    scope = _scope(session)
    request = _request(entry, document, section)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(AIDisabled):
        await service.answer(principal_id=principal.id, request=request)
    assert gateway.readiness_calls == 0
    assert gateway.calls == []
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None

    evidence = await ResearchService(session_scope_factory=scope).evidence_bundle(
        principal_id=principal.id,
        request=request,
    )
    assert evidence.evidence[0].evidence_id == "E1"


async def test_unconfigured_provider_fails_before_synthesis_plan(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "unconfigured")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="unconfigured",
        text="The evidence layer must work without a configured model provider.",
    )
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=UnconfiguredModelGateway(),
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ModelProviderUnconfigured):
        await service.answer(
            principal_id=principal.id,
            request=_request(entry, document, section),
        )
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None


@pytest.mark.parametrize(
    "readiness_state",
    [
        ModelReadinessState.PROVIDER_UNREACHABLE,
        ModelReadinessState.PROVIDER_INVALID,
        ModelReadinessState.MODEL_MISSING,
    ],
)
async def test_not_ready_provider_creates_no_plan_or_model_call(
    session: AsyncSession,
    readiness_state: ModelReadinessState,
) -> None:
    principal = await _principal(session, f"not-ready-{readiness_state.value}")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix=readiness_state.value,
        text="Grounding remains available while the configured local runtime is not ready.",
    )
    gateway = _RecordingGateway(readiness_state=readiness_state)
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ModelProviderNotReady) as captured:
        await service.answer(
            principal_id=principal.id,
            request=_request(entry, document, section),
        )

    assert captured.value.readiness.state is readiness_state
    assert gateway.readiness_calls == 1
    assert gateway.calls == []
    assert await session.scalar(select(Plan).where(Plan.principal_id == principal.id)) is None


async def test_fabricated_model_citation_is_rejected_and_failure_is_audited(
    session: AsyncSession,
) -> None:
    principal = await _principal(session, "fabricated")
    entry, document, section = await _seed_book(
        session,
        principal=principal,
        suffix="fabricated",
        text="Only canonical evidence identifier E1 exists for this answer request.",
    )
    gateway = _RecordingGateway(evidence_id="E2")
    scope = _scope(session)
    service = GroundedResearchSynthesisService(
        gateway=gateway,
        session_scope_factory=scope,
        research_service=ResearchService(session_scope_factory=scope),
    )

    with pytest.raises(ResearchEvidenceReferenceInvalid):
        await service.answer(
            principal_id=principal.id,
            request=_request(entry, document, section),
        )

    failed = await session.scalar(
        select(InteractionEvent)
        .where(
            InteractionEvent.principal_id == principal.id,
            InteractionEvent.event_type == "ai.model_failed",
        )
        .order_by(InteractionEvent.occurred_at.desc(), InteractionEvent.id.desc())
        .limit(1)
    )
    assert failed is not None
    assert failed.context["provider"] == "ollama"
    assert failed.context["model"] == "qwen3:8b"
    assert failed.context["routing"] == "local"
    assert failed.context["error_code"] == "RESEARCH_EVIDENCE_REFERENCE_INVALID"
