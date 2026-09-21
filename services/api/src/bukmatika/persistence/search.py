from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bukmatika.persistence.models import (
    Contributor,
    Subject,
    Work,
    WorkContributor,
    WorkSubject,
)


@dataclass(frozen=True, slots=True)
class CatalogWorkMatch:
    work_id: UUID
    title: str
    score: float


class CatalogSearchRepository:
    """Search the canonical PostgreSQL catalog without a second search authority."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_works(
        self,
        *,
        query: str,
        normalized_query: str,
        limit: int,
    ) -> list[CatalogWorkMatch]:
        vector = func.to_tsvector("simple", Work.canonical_title)
        ts_query = func.websearch_to_tsquery("simple", query)
        rank = func.ts_rank_cd(vector, ts_query)
        similarity = func.similarity(Work.normalized_title, normalized_query)

        author_match = (
            select(WorkContributor.work_id)
            .join(Contributor, Contributor.id == WorkContributor.contributor_id)
            .where(
                WorkContributor.work_id == Work.id,
                Contributor.normalized_name.contains(normalized_query, autoescape=True),
            )
            .exists()
        )
        subject_match = (
            select(WorkSubject.work_id)
            .join(Subject, Subject.id == WorkSubject.subject_id)
            .where(
                WorkSubject.work_id == Work.id,
                Subject.normalized_name.contains(normalized_query, autoescape=True),
            )
            .exists()
        )
        score = func.greatest(
            rank,
            similarity,
            case((author_match, 0.35), else_=0.0),
            case((subject_match, 0.25), else_=0.0),
        )

        result = await self._session.execute(
            select(Work.id, Work.canonical_title, score.label("score"))
            .where(
                or_(
                    vector.op("@@")(ts_query),
                    similarity >= 0.2,
                    author_match,
                    subject_match,
                )
            )
            .order_by(score.desc(), Work.canonical_title.asc())
            .limit(limit)
        )
        return [
            CatalogWorkMatch(
                work_id=row.id,
                title=row.canonical_title,
                score=float(row.score or 0.0),
            )
            for row in result
        ]

    async def authors_for_work(self, work_id: UUID) -> list[str]:
        result = await self._session.scalars(
            select(Contributor.display_name)
            .join(WorkContributor, WorkContributor.contributor_id == Contributor.id)
            .where(
                WorkContributor.work_id == work_id,
                WorkContributor.role == "author",
            )
            .order_by(Contributor.display_name.asc())
        )
        return list(result)
