"""Canonical PostgreSQL persistence authority for Bukmatika."""

from bukmatika.persistence.database import get_session_factory, session_scope

__all__ = ["get_session_factory", "session_scope"]
