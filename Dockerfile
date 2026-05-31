# Attest — single ASGI service. One process; state is in-memory for this v1,
# so run a single instance until a persistent store lands (see deploy/README.md).
FROM python:3.12-slim

# Don't write .pyc, don't buffer stdout (so logs reach CloudWatch promptly).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

# Install deps first against the project metadata for a cacheable layer.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 attest
USER attest

EXPOSE 8000

# --factory: create_app() builds the FastAPI instance.
CMD ["sh", "-c", "uvicorn attest.api.app:create_app --factory --host 0.0.0.0 --port ${PORT}"]
