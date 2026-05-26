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
COPY app.py database.py qa_engine.py compress_zip.py ./
COPY templates/ templates/
# Create data directories
RUN mkdir -p data uploads corrected_zips prelog_uploads logs
# Expose port 8080 (Railway routes to this port)
EXPOSE 8080
# Exec form — Railway uses port 8080; timeout 600s for large zip processing
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "600", "--keep-alive", "75", "--workers", "1", "--threads", "4", "app:app"]
