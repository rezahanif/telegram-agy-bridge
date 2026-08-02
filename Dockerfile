FROM python:3.11-slim

# Install system utilities, docker CLI, git, curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download & install Antigravity CLI (agy) inside container
RUN curl -fsSL https://antigravity.google/cli/install.sh | sh || true

COPY bot.py command_registry.py ./

ENV PATH="/root/.local/bin:${PATH}"
ENV WORKSPACE_DIR="/project"

CMD ["python", "bot.py"]
