FROM python:3.14-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/helga-agentur/worldcup-predictions"
LABEL org.opencontainers.image.description="FIFA World Cup 2026 prediction engine and static site generator."
LABEL org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

CMD ["worldcup-predictions", "--help"]
