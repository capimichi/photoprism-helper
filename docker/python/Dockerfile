FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ffmpeg \
    build-essential \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml /workspace/
RUN pip install --no-cache-dir -e .

CMD ["tail", "-f", "/dev/null"]
