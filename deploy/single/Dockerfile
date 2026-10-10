FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends nginx openssh-client ca-certificates && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app
COPY backend/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt
COPY backend/app /app/app
COPY frontend/ /usr/share/nginx/html/
RUN chmod -R a+rX /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
# Debian nginx.conf already defines server_tokens off; keep it in only one context.
RUN sed -i '/^server_tokens off;$/d' /etc/nginx/conf.d/default.conf \
 && rm -f /etc/nginx/sites-enabled/default \
 && nginx -t
COPY deploy/single/supervise.py /app/supervise.py
COPY deploy/single/health.py /app/health.py
RUN mkdir -p /app/data /app/logs /app/data/backups /run/nginx /var/lib/nginx/body \
 && chown -R app:app /app /run/nginx /var/lib/nginx
EXPOSE 9858 9859 9860 9861
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=8s --start-period=35s --retries=3 CMD python /app/health.py
CMD ["python", "/app/supervise.py"]
