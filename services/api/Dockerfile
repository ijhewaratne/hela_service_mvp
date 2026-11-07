# Hela API and worker image (one image, two commands). Cloud-neutral; see infra/deploy/README.md.
#   docker build -t hela-api:dev services/api
#   API:        docker run hela-api:dev
#   worker:     docker run hela-api:dev python -m hela.worker
#   migrations: docker run -e HELA_MIGRATION_DATABASE_URL=... hela-api:dev alembic upgrade head
# Pin the base image by digest in the release pipeline.
FROM python:3.12.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /src
COPY pyproject.toml ./
COPY hela ./hela
RUN python -m venv /opt/venv && /opt/venv/bin/pip install .

FROM python:3.12.12-slim-bookworm
ENV PATH=/opt/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HELA_ENV=staging
RUN groupadd --system --gid 10001 hela && useradd --system --uid 10001 --gid hela --no-create-home hela
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
USER 10001:10001
EXPOSE 8000
# --proxy-headers is safe only behind a proxy that overwrites X-Forwarded-*; restrict with FORWARDED_ALLOW_IPS.
CMD ["uvicorn", "hela.asgi:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
