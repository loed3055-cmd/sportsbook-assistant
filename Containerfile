FROM python:3.11-slim

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    make gcc libpq-dev git \
    && rm -rf /var/lib/apt/lists/*

# Install uv globally
RUN pip install --upgrade pip && pip install uv

WORKDIR /workspace

# Copy metadata first for cache
COPY pyproject.toml uv.lock ./

# Install deps into system Python (respect lockfile)
ENV UV_SYSTEM_PYTHON=1
RUN uv pip install --system .

# Add src to the PYTHONPATH
ENV PYTHONPATH=/workspace/src

# Copy source
COPY . .

CMD ["uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
