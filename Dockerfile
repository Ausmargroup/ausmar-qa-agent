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

# Expose port (Railway/Render set PORT env var)
EXPOSE 5000

# Use gunicorn for production
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --timeout 300 --workers 2 app:app"]
