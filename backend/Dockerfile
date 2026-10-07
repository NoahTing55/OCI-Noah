FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app

RUN mkdir -p /app/data /app/logs /app/data/backups \
    && chown -R app:app /app

USER app
EXPOSE 9858

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9858", "--proxy-headers", "--forwarded-allow-ips=*"]
