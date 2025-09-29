FROM docker.io/python:3.11-slim

# System deps
RUN apt-get update && apt-get install -y \
    build-essential libpq-dev curl git && \
    rm -rf /var/lib/apt/lists/*

# Install uv (your package manager)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Create workdir
WORKDIR /workspace

# Default command
CMD ["bash"]
