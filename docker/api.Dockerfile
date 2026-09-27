FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUKMATIKA_STORAGE_ROOT=/var/lib/bukmatika/storage

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/api /app/services/api

RUN python -m pip install --no-cache-dir /app/services/api \
    && groupadd --gid 10001 bukmatika \
    && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/bukmatika \
       --shell /usr/sbin/nologin bukmatika \
    && install -d -o bukmatika -g bukmatika /var/lib/bukmatika/storage

USER bukmatika
EXPOSE 8000

CMD ["python", "-m", "uvicorn", "bukmatika.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
