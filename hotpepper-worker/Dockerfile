FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
COPY hotpepper-worker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hotpepper-worker/hp_worker.py .
CMD ["python", "hp_worker.py"]
