# Code2Business API 服务镜像
FROM python:3.12-slim

WORKDIR /app

# 复制 wheel 并安装（c2b 依赖自动安装）
COPY dist/c2b-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/c2b-*.whl && rm /tmp/*.whl

# 数据目录（代码仓库存放处）
RUN mkdir -p /data/projects
ENV C2B_DATA_ROOT=/data

EXPOSE 8000

CMD ["uvicorn", "c2b.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
