# 控制面板容器: 一个应用 = Web 控制台 + 应用内 pipeline 循环。
# 从源码运行(PYTHONPATH=/app/src), 这样 config 的 ROOT 解析到 /app(config/ 与 data/ 都在那)。
FROM python:3.12-slim

WORKDIR /app

# 运行期依赖(不 pip install 本包, 避免 site-packages 破坏 ROOT 路径)
RUN pip install --no-cache-dir \
    'psycopg[binary]>=3.2.10' 'fastapi>=0.115' 'uvicorn[standard]>=0.34' \
    'jinja2>=3.1' 'python-multipart>=0.0.9'

COPY . .
# PYTHONUNBUFFERED: docker logs 实时输出, 不被缓冲
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1

EXPOSE 8000
# 面板(含循环)在应用内; 启停/间隔/配置/数据源都在面板里操作
CMD ["python", "-m", "folia.pipeline.cli", "panel", "--host", "0.0.0.0", "--port", "8000"]
