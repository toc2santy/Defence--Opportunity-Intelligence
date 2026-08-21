FROM python:3.12-slim

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
