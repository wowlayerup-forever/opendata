# opendata Cloud Run image: MODE=ingest or MODE=mcp (see service/app.py). Standard library only.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY tools/mcp_server.py tools/
COPY service/app.py service/
RUN useradd --system --uid 10001 app && chown -R app /app
USER app
CMD ["python", "service/app.py"]
