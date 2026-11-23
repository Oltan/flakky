FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY tools ./tools

RUN useradd --system --uid 10001 triaj
USER triaj

EXPOSE 8000
# Varsayılan: göçleri uygula ve API'yi başlat. Worker için: command: ["triaj-worker"]
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory triaj.api:app_factory --host 0.0.0.0 --port 8000"]
