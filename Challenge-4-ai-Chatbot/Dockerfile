# ==============================================================================
# Production Dockerfile for Gemini AI Chatbot on Google Cloud Run
# Base Image: Lightweight Python 3.12 slim
# Security: Non-root user execution, dynamic PORT binding, minimal footprint
# ==============================================================================

FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr and generating .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    HOME=/home/appuser

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Create a non-root user and group (UID/GID 1000) for security hardening
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -s /bin/bash -m appuser

# Copy dependency specifications first to leverage Docker layer caching
COPY requirements.txt .

# Upgrade pip and install Python packages
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and configurations
COPY . .

# Set proper ownership for non-root execution
RUN chown -R appuser:appuser /app /home/appuser

# Switch to the non-root user
USER appuser

# Expose default Cloud Run container port
EXPOSE 8080

# Configure container health check using Streamlit's internal health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/_stcore/health || exit 1

# Start Streamlit dynamically bound to Cloud Run's $PORT environment variable
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false"]
