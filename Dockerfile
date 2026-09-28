FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update && apt-get install --yes --no-install-recommends \
    bash \
    git \
    curl \
    ca-certificates \
    ffmpeg \
    libimage-exiftool-perl \
    intel-media-va-driver \
    mesa-va-drivers \
    vainfo \
    build-essential \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY photoprismhelper ./photoprismhelper

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

CMD ["tail", "-f", "/dev/null"]
