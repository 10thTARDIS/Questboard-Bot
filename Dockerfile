FROM python:3.12-slim

WORKDIR /app

# Install system dependencies (ffmpeg required for audio mixing in v0.6.0)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
RUN useradd --create-home --shell /bin/bash --uid 1001 botuser

# Install Python dependencies (separate layer for better caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY bot/ ./bot/

# Audio temp dir
RUN mkdir -p /tmp/questboard-audio && chown botuser /tmp/questboard-audio

USER botuser

CMD ["python", "-m", "bot.main"]
