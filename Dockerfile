FROM python:3.11-slim
# Install poppler (PDF-to-image) and tesseract (OCR for scanned GeoSite PDFs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Copy application code
COPY app.py database.py qa_engine.py compress_zip.py ./
COPY db_v2.py engine_common.py nhp_engine.py contract_qa_engine.py ./
COPY users_db.py ./
COPY templates/ templates/
# Create data directories
RUN mkdir -p data uploads corrected_zips prelog_uploads stage_uploads logs
# Expose port 8080 (Railway routes to this port)
EXPOSE 8080
# Exec form — Railway uses port 8080; timeout 600s for large zip processing
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "600", "--keep-alive", "75", "--workers", "1", "--threads", "4", "app:app"]
