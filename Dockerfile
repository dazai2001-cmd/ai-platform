FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ghostscript \
    libgomp1 \
    ocrmypdf \
    tesseract-ocr \
    tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir torch==2.3.0+cpu \
    --extra-index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "-m", "apps.api.main"]
