import asyncio

from bukmatika.acquisition.storage import LocalObjectStore
from bukmatika.config import get_settings
from bukmatika.processing.jobs import OcrJobWorker
from bukmatika.processing.ocr import TesseractPdfOcrEngine


async def run_worker() -> None:
    settings = get_settings()
    worker = OcrJobWorker(
        TesseractPdfOcrEngine(settings),
        LocalObjectStore(settings.storage_root),
        settings,
    )
    await worker.run()


def main() -> None:
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
