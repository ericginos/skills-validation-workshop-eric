# Use official lightweight Python 3.12 image
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Install security updates and base dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for caching
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY app.py models.py parser.py ./
COPY templates/ ./templates/
COPY transcripts/ ./transcripts/

# Create a non-root user for enterprise security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose standard Cloud Run HTTP port
EXPOSE 8080

# Production web server using Gunicorn
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 120 app:app"]
