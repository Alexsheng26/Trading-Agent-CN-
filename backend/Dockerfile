FROM python:3.11-slim

# akshare 依赖 pandas/lxml，需要编译工具；curl 用于健康检查
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 先只拷依赖声明，让 Docker 层缓存生效
COPY pyproject.toml ./
COPY app ./app

# 默认装齐两个数据源 —— "两个都接，可切换"是本项目的设计前提
RUN pip install --upgrade pip && pip install -e ".[akshare,tushare,openai]"

COPY tests ./tests

RUN mkdir -p /app/data

EXPOSE 8000

# 探测间隔刻意设小：容器刚起来时要尽快确认可用，
# 否则 compose 的 depends_on(service_healthy) 要干等一个 interval。
# start-period 给足 60 秒 —— 首次启动要建数据库表，慢一点是正常的。
HEALTHCHECK --interval=5s --timeout=5s --start-period=60s --retries=6 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
