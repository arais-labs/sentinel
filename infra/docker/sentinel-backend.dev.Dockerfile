FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        diffutils \
        dnsutils \
        fd-find \
        file \
        gawk \
        git \
        gh \
        iputils-ping \
        jq \
        less \
        moreutils \
        openssh-client \
        patch \
        ripgrep \
        rsync \
        tree \
        wget \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL https://get.docker.com | sh

WORKDIR /app

COPY apps/backend/sentinel/ ./
RUN pip install --no-cache-dir ".[dev]"
RUN playwright install --with-deps chromium
RUN chmod +x scripts/start-backend.sh

EXPOSE 8000

CMD ["./scripts/start-backend.sh"]
