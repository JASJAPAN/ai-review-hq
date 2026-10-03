FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy
WORKDIR /app
# 途中で止まってもログが残るように、出力をためずにすぐ書き出す
ENV PYTHONUNBUFFERED=1
# ファイル名を requirements.txt にしない（Webサービスのビルドが「最初に見つかった requirements.txt」を使うため）
COPY hitosara-worker/worker-requirements.txt .
RUN pip install --no-cache-dir -r worker-requirements.txt
COPY hitosara-worker/hs_worker.py .
CMD ["python", "hs_worker.py"]
