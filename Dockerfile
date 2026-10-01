FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY qloo_mcp ./qloo_mcp
COPY demo ./demo
RUN pip install --no-cache-dir ".[demo]"
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn demo.web_app:app --host 0.0.0.0 --port ${PORT}"]
