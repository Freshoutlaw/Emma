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

# Create startup script to run Ollama and Emma together
RUN echo '#!/bin/bash\n\
# Start Ollama in background (needed to proxy to Ollama Cloud)\n\
ollama serve &\n\
# Wait for Ollama to be ready\n\
echo "Waiting for Ollama to start..."\n\
for i in {1..30}; do\n\
  if curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then\n\
    echo "Ollama is ready!"\n\
    break\n\
  fi\n\
  echo "Waiting for Ollama... ($i/30)"\n\
  sleep 2\n\
done\n\
# NO local model pull - using Ollama Cloud (gemma4:31b-cloud)\n\
echo "Using Ollama Cloud model - no local model pull needed"\n\
# Start Emma\n\
exec uvicorn backend.main:app --host 0.0.0.0 --port $PORT\n\
' > /app/start.sh && chmod +x /app/start.sh

EXPOSE 8000 11434

CMD ["/app/start.sh"]
