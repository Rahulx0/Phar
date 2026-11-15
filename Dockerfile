# ---------- Stage 1: build a self-contained virtualenv ----------
# Build tools and pip cache never reach the final image.
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip 'setuptools>=78.1.1' \
    && /opt/venv/bin/pip install --no-cache-dir . \
    && /opt/venv/bin/python -m pip uninstall -y pip setuptools
    # ^ installs the app package + declared dependencies into the venv only

# ---------- Stage 2: minimal runtime ----------
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y setuptools \
    && python -m pip uninstall -y pip

ARG APP_VERSION=dev
LABEL org.opencontainers.image.title="pharmacy-api" \
      org.opencontainers.image.description="Medicine, batch and stock-movement API" \
      org.opencontainers.image.version="${APP_VERSION}"

# Run as an unprivileged user (never root)
RUN groupadd --system app && useradd --system --gid app --create-home app

COPY --from=builder /opt/venv /opt/venv

USER app
WORKDIR /home/app
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
