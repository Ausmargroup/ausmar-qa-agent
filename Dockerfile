FROM python:3.11-slim

# Install poppler for PDF-to-image conversion
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py database.py qa_engine.py ./
COPY templates/ templates/

# Create data directories
RUN mkdir -p data uploads corrected_zips prelog_uploads logs

# Expose port (DigitalOcean sets PORT env var)
EXPOSE 5000

# Use gunicorn with 1 worker + 4 threads to save memory on 512MB container
# Timeout 300s for background thread cleanup; actual requests return fast (async)
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 300 --workers 1 --threads 4 app:app"]
