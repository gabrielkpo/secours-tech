# The pipeline image: the secours_tech package, its locked dependencies, poppler and tesseract.
# It runs one command per container (migrations, load, ocr, embed, bench) and the web API
# (uvicorn --factory secours_tech.api:create_app). Base images are pinned by digest (tag in the
# comment); Dependabot moves both.

# uv only copies its binary into the build stage
FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src
# dependencies first, from the lock only, so a code change does not reinstall them
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
# then the package itself, installed as a wheel (not editable): the runtime stage needs no source
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
# poppler reads and renders the PDF pages; tesseract checks suspect text layers (its French
# model is fetched into data/tessdata and checked by SHA-256 on first use). Debian packages are
# fetched over HTTPS, and apt also checks their signatures.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
# an unprivileged user with a fixed id, so Kubernetes can check runAsNonRoot
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
# the commands read corpus/ (sources manifest, OCR gate record) and data/ relative to the working
# directory; data/ (PDFs, OCR model, cases) is mounted at run time and never baked in
COPY corpus ./corpus
RUN mkdir data && chown app:app data
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
# default: apply the migrations to DATABASE_URL; any other module is passed as the command
CMD ["python", "-m", "secours_tech.db"]
