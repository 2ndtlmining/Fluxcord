FROM python:3.11-alpine

WORKDIR /app

# Build dependencies for packages that need compilation
RUN apk add --no-cache gcc musl-dev libffi-dev

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Persistent volume for SQLite state database
VOLUME /data

ENV DB_PATH=/data/fluxcord.db \
    POLL_INTERVAL_MINUTES=5 \
    DAILY_HOUR_UTC=8 \
    NODE_REQUEST_TIMEOUT=10 \
    API_REQUEST_TIMEOUT=30

CMD ["python", "-m", "src.main"]
