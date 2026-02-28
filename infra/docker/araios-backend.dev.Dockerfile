FROM python:3.12-slim

WORKDIR /app

COPY apps/backend/araios/ ./
RUN pip install --no-cache-dir ".[dev]"

EXPOSE 9000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000", "--reload"]
