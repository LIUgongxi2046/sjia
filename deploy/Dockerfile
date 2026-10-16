# 后端与 Worker 共用镜像参考。
# 基础镜像版本在 T28/T33 按批准环境与依赖锁文件核验。
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.lock.txt ./requirements.lock.txt
RUN pip install -r requirements.lock.txt

COPY backend /app/backend
COPY backend-design/*.schema.json /app/backend-design/
COPY alert-design/*.schema.json /app/alert-design/
COPY data-design/*.schema.json /app/data-design/
COPY data-design/v2-schema.sql /app/data-design/v2-schema.sql
COPY deploy/smoke_runtime.py /app/deploy/smoke_runtime.py
COPY deploy/probe_runtime.py /app/deploy/probe_runtime.py
COPY deploy/start_api.py /app/deploy/start_api.py
COPY deploy/gunicorn.conf.py /app/deploy/gunicorn.conf.py

WORKDIR /app/backend

RUN mkdir -p /app/backend/staticfiles \
    && DJANGO_SETTINGS_MODULE=config.settings.development python /app/deploy/smoke_runtime.py

EXPOSE 8000

CMD ["python", "/app/deploy/start_api.py"]
