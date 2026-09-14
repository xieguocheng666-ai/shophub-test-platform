# 被测系统（SUT）镜像：FastAPI 电商后端
FROM python:3.11-slim

WORKDIR /app

# 先装依赖，代码改动时利用 Docker 层缓存不重装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 启动：先建库建表插种子（幂等），再起 uvicorn
CMD ["sh", "-c", "python -c 'from server.db import init_db; init_db()' && uvicorn server.main:app --host 0.0.0.0 --port 8000"]
