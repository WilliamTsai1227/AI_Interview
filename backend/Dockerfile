# API 與 worker 共用的映像（build context 是專案根目錄，才能把 frontend/ 一起放進來）
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/tests ./tests
COPY frontend /frontend
ENV FRONTEND_DIR=/frontend

RUN useradd --create-home --uid 1000 app
USER app

EXPOSE 8009
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8009", "--proxy-headers"]
