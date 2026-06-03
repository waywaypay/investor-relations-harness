# Stage 1 — build the React workspace.
# The inlined SPA bundle (src/attest/api/static/index.html) is generated here
# so the Python package always ships the freshest build and the file never needs
# to be checked in manually.
FROM node:22-slim AS spa-builder
WORKDIR /build
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
# Build Vite bundles, then inline JS/CSS into one self-contained index.html that
# the FastAPI frontend module serves at /.
RUN npm run build

# Stage 2 — Python service.
FROM python:3.12-slim

# Don't write .pyc, don't buffer stdout.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Copy Python project first (cacheable layer when only web/ changed is now separate).
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts/build_spa.py scripts/build_spa.py

# Copy the Vite output from the builder stage and inline it into the static file
# the package bundles (api/static/index.html). The package-data glob in
# pyproject.toml picks it up during `pip install` below.
COPY --from=spa-builder /build/dist ./web/dist
RUN python scripts/build_spa.py --no-build

RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 attest
USER attest

EXPOSE 8000

CMD ["sh", "-c", "uvicorn attest.api.app:app --host 0.0.0.0 --port ${PORT}"]
