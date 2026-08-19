FROM python:3.12.13-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create a non-root user
RUN groupadd -r just1kbot && useradd -r -g just1kbot -d /app -s /sbin/nologin just1kbot

WORKDIR /app

# Install system dependencies if any are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt requirements.lock ./

# Install Python dependencies from lock file
RUN pip install --no-cache-dir --no-deps --require-hashes -r requirements.lock

# Copy the rest of the application
COPY --chown=just1kbot:just1kbot . .

# Ensure entrypoint is executable
RUN chmod +x docker-entrypoint.sh

# Switch to non-root user
USER just1kbot

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/health || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["bot"]
