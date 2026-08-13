# syntax=docker/dockerfile:1

# Один Long Poll-воркер, без входящего HTTP (см. DEPLOY.md/ARCHITECTURE.md,
# "Деплой"). python:3.12-slim: локальный venv на 3.14, но в коде нет ничего
# 3.14-специфичного — 3.12 то, что уже проверено на хосте library-vps
# (системный Python), берём его как стабильную, маленькую базу.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
