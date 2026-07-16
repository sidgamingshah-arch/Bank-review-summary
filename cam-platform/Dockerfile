# One image, eight deployables: every service runs this image with a different
# SERVICE_MODULE (see docker-compose.yml). Independently deployable at runtime;
# split into per-service images later without touching code (ADR-0001).
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[postgres,anthropic]"

ENV SERVICE_MODULE=cam.gateway.main:app \
    PORT=8000 \
    CAM_DATA_DIR=/data
VOLUME /data

CMD ["sh", "-c", "uvicorn $SERVICE_MODULE --host 0.0.0.0 --port $PORT"]
