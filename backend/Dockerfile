FROM node:24-bookworm-slim AS node
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/backend

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY backend/pyproject.toml backend/requirements.txt ./
COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY frontend/server /srv/frontend/server
COPY frontend/src /srv/frontend/src
RUN mkdir -p data && pip install --no-cache-dir .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9090"]
