FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        fluxbox \
        novnc \
        websockify \
        x11vnc \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/backend/sentinel/ ./
RUN pip install --no-cache-dir ".[dev]"
RUN playwright install --with-deps chromium
RUN chmod +x scripts/start-backend.sh

EXPOSE 8000
EXPOSE 6080

CMD ["./scripts/start-backend.sh"]
