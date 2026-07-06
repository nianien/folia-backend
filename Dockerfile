# 控制面板容器: 一个应用 = Web 控制台 + 应用内 pipeline 循环。
# 从源码运行(PYTHONPATH=/app/src), 这样 config 的 ROOT 解析到 /app(data/ 在那)。
FROM python:3.12-slim

WORKDIR /app

# uv: 从官方镜像拷二进制(固定版本, 可复现), 仅构建期用来按 uv.lock 装依赖
COPY --from=ghcr.io/astral-sh/uv:0.11.20 /uv /bin/uv

# 先只拷依赖声明命中缓存层; 只装依赖不装本包(保持从 /app/src 源码运行, 不破坏 ROOT)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .
# .venv/bin 优先 → python 即带依赖的 venv 解释器; PYTHONPATH 让 folia 从源码可导入
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8000
# 面板(含循环)在应用内; 启停/间隔/配置/数据源都在面板里操作
CMD ["python", "-m", "folia.pipeline.cli", "panel", "--host", "0.0.0.0", "--port", "8000"]
