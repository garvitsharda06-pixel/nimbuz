FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/pyproject.toml backend/
COPY backend/dalal backend/dalal
RUN pip install --no-cache-dir ./backend
COPY config config
COPY docs docs
COPY --from=ui /ui/dist frontend/dist
# The API binds to 0.0.0.0 INSIDE the container only; docker-compose publishes it on 127.0.0.1.
ENV DALAL_HOME=/app DALAL_CONFIG_DIR=/app/config DALAL_UI_DIST=/app/frontend/dist
EXPOSE 8000
CMD ["dalal", "serve"]
