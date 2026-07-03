# 定时管道容器: 常驻, 每隔 PIPELINE_INTERVAL 秒跑一轮 run-once(+入库)。
# 从源码运行(PYTHONPATH=/app/src), 这样 config 的 ROOT 解析到 /app(config/ 与 data/ 都在那)。
FROM python:3.12-slim

WORKDIR /app

# 只装运行期依赖(入库要用); 包本身从源码跑, 不 pip install(避免 site-packages 破坏 ROOT 路径)
RUN pip install --no-cache-dir 'psycopg[binary]>=3.2.10'

COPY . .
ENV PYTHONPATH=/app/src

CMD ["bash", "scripts/pipeline-loop.sh"]
