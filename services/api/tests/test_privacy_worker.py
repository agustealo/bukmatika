import asyncio

import pytest

from bukmatika.privacy.worker import PrivacyErasureWorker


class _CleanupService:
    def __init__(self) -> None:
        self.calls = 0
        self.retried = asyncio.Event()

    async def cleanup_pending_storage(self) -> dict[str, int]:
        self.calls += 1
        if self.calls == 1:
            raise OSError("transient storage failure")
        self.retried.set()
        return {"deleted": 1, "pending": 0}


@pytest.mark.asyncio
async def test_privacy_erasure_worker_survives_transient_cleanup_failure() -> None:
    service = _CleanupService()
    worker = PrivacyErasureWorker(service, poll_seconds=0.01)  # type: ignore[arg-type]
    task = asyncio.create_task(worker.run())

    await asyncio.wait_for(service.retried.wait(), timeout=1)
    assert service.calls >= 2

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
