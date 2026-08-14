FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for Ollama
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Ollama binary (needed to proxy to Ollama Cloud)
RUN curl -fsSL https://ollama.com/install.sh | sh

# Set Ollama home directory
ENV OLLAMA_HOME=/app/.ollama
ENV OLLAMA_HOST=0.0.0.0:11434
RUN mkdir -p $OLLAMA_HOME

# Install Python dependencies
COPY requirements.txt ./
ENV PIP_DEFAULT_TIMEOUT=100 \
    PIP_RETRIES=6
COPY infrastructure/wheels /wheelhouse
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --find-links /wheelhouse -r requirements.txt

# Copy the project
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

# Startup script: starts Ollama, exports the Ollama Cloud API key, pre-pulls
# the fallback + embedding models, and fails fast if Ollama isn't ready
COPY infrastructure/start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 8000 11434

CMD ["/app/start.sh"]
