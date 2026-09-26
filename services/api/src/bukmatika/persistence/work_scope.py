from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.sql.elements import ColumnElement

from bukmatika.persistence.models import Work
from bukmatika.persistence.private_work_models import PrivateWorkScope


def catalog_work_clause() -> ColumnElement[bool]:
    private_scope = select(PrivateWorkScope.work_id).where(
        PrivateWorkScope.work_id == Work.id
    )
    return ~private_scope.exists()


def principal_work_clause(principal_id: UUID) -> ColumnElement[bool]:
    owned_private_scope = select(PrivateWorkScope.work_id).where(
        PrivateWorkScope.work_id == Work.id,
        PrivateWorkScope.owner_principal_id == principal_id,
    )
    return or_(catalog_work_clause(), owned_private_scope.exists())


__all__ = ["catalog_work_clause", "principal_work_clause"]
