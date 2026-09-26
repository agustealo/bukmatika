from uuid import UUID

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from bukmatika.persistence.models import Base, TimestampMixin


class PrivateWorkScope(Base, TimestampMixin):
    """One-to-one owner authority for catalog identities that are principal-private."""

    __tablename__ = "private_work_scopes"
    __table_args__ = (
        Index("ix_private_work_scopes_owner", "owner_principal_id", "work_id"),
    )

    work_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("works.id", ondelete="CASCADE"),
        primary_key=True,
    )
    owner_principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="CASCADE"),
        nullable=False,
    )


__all__ = ["PrivateWorkScope"]
