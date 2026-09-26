import asyncio

import structlog

from bukmatika.privacy.service import AccountPrivacyService

_logger = structlog.get_logger(__name__)


class PrivacyErasureWorker:
    """Continuously drain durable privacy-erasure work without depending on user traffic."""

    def __init__(
        self,
        service: AccountPrivacyService,
        *,
        poll_seconds: float,
    ) -> None:
        self._service = service
        self._poll_seconds = poll_seconds

    async def run(self) -> None:
        while True:
            try:
                await self._service.cleanup_pending_storage()
            except Exception:
                _logger.exception("privacy_erasure_cleanup_failed")
            await asyncio.sleep(self._poll_seconds)


__all__ = ["PrivacyErasureWorker"]
