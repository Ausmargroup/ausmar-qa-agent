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

# Expose port 8080 (Railway default for Docker deployments)
EXPOSE 8080

# Use exec form (no shell) so PORT env var is never misinterpreted as a literal string
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "300", "--workers", "1", "--threads", "4", "app:app"]
