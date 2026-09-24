# Production Dockerfile for Alaska Department of Snow (ADS) Unstructured Document Synthesis System
FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered output for Cloud Logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Install system dependencies (curl for container healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root system user and group (adsuser)
RUN groupadd -g 1001 adsgroup && \
    useradd -u 1001 -g adsgroup -m -s /bin/bash adsuser

WORKDIR /app

# Install Python dependencies with layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY --chown=adsuser:adsgroup . .

# Ensure non-root ownership
RUN chown -R adsuser:adsgroup /app

# Switch to non-root user for runtime security
USER adsuser

# Cloud Run dynamic port binding
EXPOSE 8080

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/_stcore/health || exit 1

# Launch Streamlit with dynamic $PORT binding
ENTRYPOINT ["sh", "-c", "exec streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --browser.gatherUsageStats=false"]
