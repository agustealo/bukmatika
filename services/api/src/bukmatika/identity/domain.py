from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class SessionResponse(BaseModel):
    principal_id: UUID
    session_id: UUID
    expires_at: datetime
