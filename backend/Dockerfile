FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install poetry==1.8.4

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev dependencies in production)
RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi --no-dev

# Copy application code
COPY src ./src

# Expose port (Railway will override with PORT env var)
EXPOSE ${PORT:-8000}

# Run the application using PORT env var (Railway requirement)
CMD uvicorn src.app:app --host 0.0.0.0 --port ${PORT:-8000}
