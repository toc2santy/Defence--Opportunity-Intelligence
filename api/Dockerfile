FROM python:3.12-slim

# postgresql-client is here ONLY for pg_dump (app/backup.py, 2026-09
# Backup & DR Phase 1) — python:3.12-slim has no Postgres tooling at
# all by default, and backup exists specifically to survive this
# container/host being lost, so shelling out to the same DB image's
# own dump tool (version-matched to postgres:16 in docker-compose.yml)
# is the correct approach rather than a pure-Python re-implementation
# of pg_dump's binary format.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Mirrors the host project layout exactly (project_root/api/...,
# project_root/db/...) rather than flattening api/ away — this is
# what lets the SAME Path(__file__).resolve().parents[3] arithmetic
# in the Alembic revisions correctly find db/ whether they're run
# on the host or inside this container. A flattened layout here
# would silently break every Alembic revision's file lookup.
WORKDIR /project

COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt

COPY api api
COPY db db

WORKDIR /project/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
