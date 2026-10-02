# Optional registry prefix for the base images, e.g. a mirror
# ("<host>/<path>/"). Must include a trailing slash. Empty by default, so
# builds pull straight from Docker Hub.
ARG BASE_REGISTRY=

# The Python images for the dependency stage and the runtime stage. Both
# default to python:3.14-slim. To build on a hardened, shell-less runtime
# instead, set them to a matching Docker Hardened Images pair: the "-dev"
# variant with a shell and pip to install into, and its runtime sibling to
# ship. docker-compose.dhi.yml and CI's USE_DHI=true both do this.
#
#   --build-arg PYTHON_BUILDER_IMAGE=dhi.io/python:3.14-debian13-dev
#   --build-arg PYTHON_RUNTIME_IMAGE=dhi.io/python:3.14-debian13
#
# Both must use the same libc: Pillow, bcrypt and sqlcipher3 install as glibc
# (manylinux) wheels, which slim and DHI's debian13 variant share.
ARG PYTHON_BUILDER_IMAGE=${BASE_REGISTRY}python:3.14-slim
ARG PYTHON_RUNTIME_IMAGE=${BASE_REGISTRY}python:3.14-slim

# Stage 1: Build frontend
FROM ${BASE_REGISTRY}node:22-alpine AS frontend-build
# Package source for npm. Unset, npm uses its default registry; CI passes the
# internal mirror through when one is configured.
ARG NPM_CONFIG_REGISTRY
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python dependencies.
#
# --target into a plain directory rather than the interpreter's site-packages:
# the runtime stage may be a different image (DHI keeps its interpreter under
# /usr, slim under /usr/local), and a directory on PYTHONPATH works on either.
FROM ${PYTHON_BUILDER_IMAGE} AS python-deps
# Package source for pip. Unset, pip uses PyPI; CI passes the internal mirror
# through when one is configured. Build-time only: not baked into the image.
ARG PIP_INDEX_URL
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --target /opt/pydeps -r /tmp/requirements.txt

# Stage 3: Python runtime.
#
# No RUN steps: the hardened runtime has no shell, so everything here is a
# COPY or metadata and the stage builds the same way on either base.
FROM ${PYTHON_RUNTIME_IMAGE}
WORKDIR /app
ENV PYTHONPATH=/opt/pydeps \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=python-deps /opt/pydeps /opt/pydeps
COPY backend/ .
COPY --from=frontend-build /app/dist ./static
# Executable bits set at copy time; there is no chmod to run afterwards.
COPY --chmod=0755 scripts/ ./scripts/
# Product docs served by the in-app reader (/api/docs).
COPY docs/ ./docs/
# The browser extension offered for download (/api/extension), vendored from its
# own repo by scripts/vendor-extension.sh.
COPY extension/ ./extension/

# Cove runs as root, on both bases. It stages helper scripts into the
# workspace storage tree and extracts migrated workspace homes with their
# ownership forced to the workspace PUID/PGID, which needs root. The hardened
# base defaults to a non-root user, so this is set explicitly.
USER 0:0

EXPOSE 8080

# `python -m`: uvicorn's launcher script is in /opt/pydeps/bin, not on PATH.
CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
