FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
# Retry/backoff so transient PyPI timeouts don't fail the build; the
# BuildKit cache mount lets an interrupted build resume its downloads.
# Pre-seed huge wheels (e.g. opencv ~74 MB) in infrastructure/wheels/
# (gitignored) and pip installs them locally instead of over a flaky
# network; an empty wheelhouse is a no-op.
ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=6
COPY infrastructure/wheels /wheelhouse
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --find-links /wheelhouse -r requirements.txt

# Copy the project (excluding data/, which is a runtime volume).
COPY backend/ backend/
COPY agents/ agents/
COPY capabilities/ capabilities/
COPY security/ security/
COPY memory/ memory/
COPY llm/ llm/
COPY orchestration/ orchestration/
COPY cost/ cost/
COPY performance/ performance/
COPY design/ design/
COPY interfaces/ interfaces/
COPY flags/ flags/

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
