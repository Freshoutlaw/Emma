FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the project (excluding data/, which is a runtime volume).
COPY backend/ backend/
COPY agents/ agents/
COPY capabilities/ capabilities/
COPY security/ security/
COPY memory/ memory/
COPY llm/ llm/
COPY interfaces/ interfaces/
COPY flags/ flags/

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
