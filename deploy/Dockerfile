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
COPY data-design/v2-schema.sql /app/data-design/v2-schema.sql

WORKDIR /app/backend

RUN mkdir -p /app/backend/staticfiles

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
