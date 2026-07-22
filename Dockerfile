# Optional registry prefix for the base images, e.g. a GitLab Dependency Proxy
# ("<host>/<group>/dependency_proxy/containers/") so CI pulls through a cache and
# avoids Docker Hub rate limits. Must include a trailing slash. Empty by default,
# so local builds pull straight from Docker Hub.
ARG BASE_REGISTRY=

# Stage 1: Build frontend
FROM ${BASE_REGISTRY}node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM ${BASE_REGISTRY}python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY --from=frontend-build /app/dist ./static
COPY scripts/ ./scripts/
# Product docs served by the in-app reader (/api/docs).
COPY docs/ ./docs/

RUN chmod +x ./scripts/*.sh

EXPOSE 8080

CMD ["uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
