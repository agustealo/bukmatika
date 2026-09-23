from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence import session_scope
from bukmatika.persistence.personalization import PersonalizationRepository
from bukmatika.persistence.personalization_models import PreferenceClaim
from bukmatika.personalization.domain import (
    ContextGoal,
    ContextLibraryEntry,
    ContextManifest,
    ContextPreference,
    ContextRequest,
    ContextTask,
    PreferenceClaimResponse,
    PreferenceInfluence,
    PreferenceKey,
    PreferenceScopeType,
)

SessionScopeFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
ResearchAnswerAvailability = Callable[[], Awaitable[bool]]

_TASK_CAPABILITIES: dict[ContextTask, tuple[str, ...]] = {
    ContextTask.DISCOVERY: (
        "discovery.search",
        "catalog.search",
        "preferences.propose",
    ),
    ContextTask.LIBRARY: (
        "library.list",
        "reader.open",
        "preferences.propose",
    ),
    ContextTask.RESEARCH: (
        "research.search",
        "reader.open",
        "preferences.propose",
    ),
    ContextTask.READER: (
        "reader.open",
        "research.search",
        "preferences.propose",
    ),
}

_TASK_SCOPE: dict[ContextTask, PreferenceScopeType | None] = {
    ContextTask.DISCOVERY: PreferenceScopeType.DISCOVERY,
    ContextTask.LIBRARY: None,
    ContextTask.RESEARCH: PreferenceScopeType.RESEARCH,
    ContextTask.READER: PreferenceScopeType.READER,
}


class ContextAssembler:
    """Deterministically assembles the smallest inspectable future-model context."""

    def __init__(
        self,
        *,
        session_scope_factory: SessionScopeFactory = session_scope,
        research_answer_available: bool = False,
        research_answer_availability: ResearchAnswerAvailability | None = None,
    ) -> None:
        self._session_scope = session_scope_factory
        self._research_answer_available = research_answer_available
        self._research_answer_availability = research_answer_availability

    async def assemble(
        self,
        *,
        principal_id: UUID,
        request: ContextRequest,
    ) -> ContextManifest:
        async with self._session_scope() as database_session:
            repository = PersonalizationRepository(database_session)
            user_model = await repository.get_or_create_user_model(principal_id)

            if not user_model.ai_enabled:
                return ContextManifest(
                    task=request.task,
                    ai_enabled=False,
                    learning_enabled=user_model.learning_enabled,
                    autonomy_level=user_model.autonomy_level,
                    model_context_ready=False,
                    preferences=[],
                    goal=None,
                    library_entries=[],
                    available_capabilities=[],
                    exclusion_reasons=["ai_disabled"],
                )

            goal = None
            if request.goal_id is not None:
                stored_goal = await repository.active_goal(
                    principal_id=principal_id,
                    goal_id=request.goal_id,
                )
                goal = ContextGoal(
                    goal_id=stored_goal.id,
                    title=stored_goal.title,
                    kind=stored_goal.kind,
                    scope=stored_goal.scope,
                    constraints=stored_goal.constraints,
                    inclusion_reason="Explicitly selected active goal owned by this principal.",
                )

            library_entries = [
                ContextLibraryEntry(
                    library_entry_id=context.library_entry_id,
                    work_id=context.work_id,
                    edition_id=context.edition_id,
                    title=context.title,
                    document_ids=list(context.document_ids),
                    inclusion_reason=(
                        "Explicitly selected library entry validated against principal ownership."
                    ),
                )
                for context in await repository.owned_library_contexts(
                    principal_id=principal_id,
                    library_entry_ids=request.library_entry_ids,
                )
            ]

            preferences = self._effective_preferences(
                await repository.active_claims(principal_id),
                request=request,
            )
            manifest = ContextManifest(
                task=request.task,
                ai_enabled=True,
                learning_enabled=user_model.learning_enabled,
                autonomy_level=user_model.autonomy_level,
                preferences=preferences,
                goal=goal,
                library_entries=library_entries,
                available_capabilities=list(_TASK_CAPABILITIES[request.task]),
                exclusion_reasons=[],
                model_context_ready=True,
            )

        if request.task not in (ContextTask.RESEARCH, ContextTask.READER):
            return manifest

        answer_available = self._research_answer_available
        if self._research_answer_availability is not None:
            answer_available = await self._research_answer_availability()
        if not answer_available:
            return manifest
        return manifest.model_copy(
            update={
                "available_capabilities": [
                    *manifest.available_capabilities,
                    "research.answer",
                ]
            }
        )

    def _effective_preferences(
        self,
        claims: list[PreferenceClaim],
        *,
        request: ContextRequest,
    ) -> list[ContextPreference]:
        applicable: dict[str, tuple[int, datetime, PreferenceClaim, str]] = {}
        exact_scopes = {(scope.scope_type.value, scope.scope_value) for scope in request.scopes}
        if request.goal_id is not None:
            exact_scopes.add((PreferenceScopeType.GOAL.value, str(request.goal_id)))
        task_scope = _TASK_SCOPE[request.task]

        for claim in claims:
            if claim.source != "explicit":
                continue
            matched = self._match_claim(
                claim,
                task_scope=task_scope,
                exact_scopes=exact_scopes,
            )
            if matched is None:
                continue
            specificity, reason = matched
            existing = applicable.get(claim.key)
            rank = (specificity, claim.updated_at)
            if existing is None or rank > (existing[0], existing[1]):
                applicable[claim.key] = (
                    specificity,
                    claim.updated_at,
                    claim,
                    reason,
                )

        return [
            self._context_preference(applicable[key][2], applicable[key][3])
            for key in sorted(applicable)
        ]

    @staticmethod
    def _match_claim(
        claim: PreferenceClaim,
        *,
        task_scope: PreferenceScopeType | None,
        exact_scopes: set[tuple[str, str]],
    ) -> tuple[int, str] | None:
        if claim.scope_type == PreferenceScopeType.GLOBAL.value:
            return 0, "Explicit global preference applies to this task."
        if (
            task_scope is not None
            and claim.scope_type == task_scope.value
            and claim.scope_value == ""
        ):
            return 1, f"Explicit {task_scope.value} preference applies to this task."
        if (claim.scope_type, claim.scope_value) in exact_scopes:
            return (
                2,
                "Explicit preference matched the current "
                f"{claim.scope_type} scope '{claim.scope_value}'.",
            )
        return None

    @staticmethod
    def _context_preference(claim: PreferenceClaim, reason: str) -> ContextPreference:
        response = PreferenceClaimResponse.model_validate(
            {
                "claim_id": claim.id,
                "key": claim.key,
                "value": claim.value,
                "source": claim.source,
                "status": claim.status,
                "confidence": claim.confidence,
                "scope_type": claim.scope_type,
                "scope_value": claim.scope_value,
                "influence": PreferenceInfluence.model_validate(claim.influence),
                "evidence_count": claim.evidence_count,
                "first_observed_at": claim.first_observed_at,
                "last_reinforced_at": claim.last_reinforced_at,
                "created_at": claim.created_at,
                "updated_at": claim.updated_at,
            }
        )
        return ContextPreference(
            claim_id=response.claim_id,
            key=PreferenceKey(response.key),
            value=response.value,
            source=response.source,
            confidence=response.confidence,
            scope_type=response.scope_type,
            scope_value=response.scope_value,
            influence=response.influence,
            inclusion_reason=reason,
        )
