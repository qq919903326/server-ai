FROM node:24-alpine AS ui
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt ./
COPY database_mcp/requirements.txt database_mcp/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ backend/
COPY database_mcp/ database_mcp/
COPY --from=ui /build/dist frontend/dist/
ENV AI_DATA_DIR=/app/data
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
