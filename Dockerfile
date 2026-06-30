FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md AGENTS.md ./
COPY src ./src

RUN pip install --no-cache-dir -e .

CMD ["worldcup-predictions", "--help"]
