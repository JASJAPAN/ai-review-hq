FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY hitosara-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hitosara-worker/hs_worker.py .
CMD ["python", "hs_worker.py"]
