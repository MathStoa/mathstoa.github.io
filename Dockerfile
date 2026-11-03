FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt \
    && groupadd --gid 10001 mathstoa \
    && useradd --uid 10001 --gid mathstoa --create-home mathstoa

COPY --chown=mathstoa:mathstoa . /app
USER mathstoa

EXPOSE 8000

# Run one application replica while using this startup migration command.
# Larger deployments should run migrations once as a separate release job.
# Request logs omit URLs, which can contain callback codes or invitation tokens.
CMD ["sh", "-c", "python server/manage.py migrate --noinput && python server/manage.py createcachetable && python server/manage.py collectstatic --noinput && exec python server/manage.py run_web"]
