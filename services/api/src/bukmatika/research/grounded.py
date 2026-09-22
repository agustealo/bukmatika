from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.ai.gateway import (
    ModelDataClassification,
    ModelGateway,
    ModelRequest,
    ModelTask,
    build_default_model_gateway,
    model_gateway_identity,
)
from bukmatika.ai.service import AIContextUnavailable, AIDisabled
from bukmatika.config import Settings, get_settings
from bukmatika.persistence import session_scope
from bukmatika.persistence.events import InteractionEventRepository, SemanticEventType
from bukmatika.personalization.context import ContextAssembler
from bukmatika.personalization.domain import ContextRequest, ContextTask
from bukmatika.research.domain import (
    GroundedAnswerDraft,
    GroundedAnswerResponse,
    GroundedAnswerStatus,
    GroundedCitationResponse,
    GroundedClaimResponse,
    GroundedResearchRequest,
    ResearchPassageResponse,
    ResearchSearchRequest,
)
from bukmatika.research.service import ResearchService

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class GroundedCitationInvalid(RuntimeError):
    code = "GROUNDED_CITATION_INVALID"


class GroundedResearchService:
    """Retrieval-first grounded synthesis over explicitly selected owned books."""

    def __init__(
        self,
        *,
        gateway: ModelGateway | None = None,
        research_service: ResearchService | None = None,
        context_assembler: ContextAssembler | None = None,
        settings: Settings | None = None,
        session_scope_factory: SessionScopeFactory = session_scope,
    ) -> None:
        self._settings = settings or get_settings()
        self._gateway = gateway or build_default_model_gateway(settings=self._settings)
        self._research = research_service or ResearchService(
            session_scope_factory=session_scope_factory
        )
        self._context = context_assembler or ContextAssembler(
            session_scope_factory=session_scope_factory
        )
        self._session_scope = session_scope_factory

    async def answer(
        self,
        *,
        principal_id: UUID,
        request: GroundedResearchRequest,
    ) -> GroundedAnswerResponse:
        context = await self._context.assemble(
            principal_id=principal_id,
            request=ContextRequest(
                task=ContextTask.RESEARCH,
                library_entry_ids=request.library_entry_ids,
            ),
        )
        if not context.ai_enabled:
            raise AIDisabled("AI is disabled for this principal")
        if not context.model_context_ready:
            raise AIContextUnavailable("Model context is not available")

        max_passages = min(
            request.max_passages,
            self._settings.grounded_answer_max_passages,
        )
        retrieval = await self._research.search(
            principal_id=principal_id,
            request=ResearchSearchRequest(
                query=request.question,
                library_entry_ids=request.library_entry_ids,
                limit=max_passages,
            ),
        )
        if not retrieval.passages:
            return GroundedAnswerResponse(
                status=GroundedAnswerStatus.NO_EVIDENCE,
                question=request.question,
                selected_library_entry_ids=request.library_entry_ids,
                retrieval_count=0,
                claims=[],
                provider=None,
                model=None,
            )

        evidence_packets = [
            _model_evidence_packet(
                passage,
                max_chars=self._settings.grounded_answer_max_passage_chars,
            )
            for passage in retrieval.passages
        ]
        model_request = ModelRequest.model_validate(
            {
                "task": ModelTask.GROUNDED_RESEARCH.value,
                "payload": {
                    "question": request.question,
                    "preferences": [
                        preference.model_dump(
                            mode="json",
                            include={
                                "key",
                                "value",
                                "source",
                                "confidence",
                                "scope_type",
                                "scope_value",
                            },
                        )
                        for preference in context.preferences
                    ],
                    "evidence": evidence_packets,
                },
                "data_classification": ModelDataClassification.PRIVATE_USER_CONTEXT.value,
                "max_output_tokens": self._settings.grounded_answer_max_output_tokens,
                "timeout_seconds": self._settings.model_timeout_seconds,
            }
        )
        draft = await self._gateway.generate_structured(model_request, GroundedAnswerDraft)
        provider, model = model_gateway_identity(self._gateway)

        if draft.insufficient_evidence:
            response = GroundedAnswerResponse(
                status=GroundedAnswerStatus.INSUFFICIENT_EVIDENCE,
                question=request.question,
                selected_library_entry_ids=request.library_entry_ids,
                retrieval_count=len(retrieval.passages),
                claims=[],
                provider=provider,
                model=model,
            )
            await self._record_completion(
                principal_id=principal_id,
                response=response,
                cited_evidence_ids=[],
            )
            return response

        by_evidence_id = {passage.chunk_id: passage for passage in retrieval.passages}
        claims: list[GroundedClaimResponse] = []
        cited_ids: list[UUID] = []
        for claim in draft.claims:
            unknown = [
                evidence_id
                for evidence_id in claim.evidence_ids
                if evidence_id not in by_evidence_id
            ]
            if unknown:
                raise GroundedCitationInvalid(
                    "Model cited evidence outside the retrieved selected-book evidence set"
                )

            citations = [
                _citation_response(
                    by_evidence_id[evidence_id],
                    max_chars=self._settings.grounded_answer_max_passage_chars,
                )
                for evidence_id in claim.evidence_ids
            ]
            cited_ids.extend(claim.evidence_ids)
            claims.append(GroundedClaimResponse(text=claim.text, citations=citations))

        response = GroundedAnswerResponse(
            status=GroundedAnswerStatus.GROUNDED,
            question=request.question,
            selected_library_entry_ids=request.library_entry_ids,
            retrieval_count=len(retrieval.passages),
            claims=claims,
            provider=provider,
            model=model,
        )
        await self._record_completion(
            principal_id=principal_id,
            response=response,
            cited_evidence_ids=list(dict.fromkeys(cited_ids)),
        )
        return response

    async def _record_completion(
        self,
        *,
        principal_id: UUID,
        response: GroundedAnswerResponse,
        cited_evidence_ids: list[UUID],
    ) -> None:
        async with self._session_scope() as database_session:
            await InteractionEventRepository(database_session).record(
                SemanticEventType.RESEARCH_GROUNDED_ANSWER_COMPLETED,
                principal_id=principal_id,
                entity_type="library_selection",
                context={
                    "question": response.question,
                    "selected_library_entry_ids": [
                        str(entry_id) for entry_id in response.selected_library_entry_ids
                    ],
                    "status": response.status.value,
                    "retrieval_count": response.retrieval_count,
                    "claim_count": len(response.claims),
                    "cited_evidence_ids": [str(evidence_id) for evidence_id in cited_evidence_ids],
                    "provider": response.provider,
                    "model": response.model,
                },
            )


def _model_evidence_packet(
    passage: ResearchPassageResponse,
    *,
    max_chars: int,
) -> dict[str, object]:
    return {
        "evidence_id": str(passage.chunk_id),
        "work_title": passage.work_title,
        "edition_title": passage.edition_title,
        "heading": passage.heading,
        "locator": passage.locator,
        "text": _bounded_text(passage.text, max_chars=max_chars),
    }


def _citation_response(
    passage: ResearchPassageResponse,
    *,
    max_chars: int,
) -> GroundedCitationResponse:
    return GroundedCitationResponse(
        evidence_id=passage.chunk_id,
        library_entry_id=passage.library_entry_id,
        work_id=passage.work_id,
        work_title=passage.work_title,
        edition_id=passage.edition_id,
        edition_title=passage.edition_title,
        document_id=passage.document_id,
        chunk_id=passage.chunk_id,
        section_id=passage.section_id,
        heading=passage.heading,
        locator=passage.locator,
        char_start=passage.char_start,
        char_end=passage.char_end,
        text=_bounded_text(passage.text, max_chars=max_chars),
    )


def _bounded_text(text: str, *, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()
