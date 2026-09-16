# Runtime uses Debian's prebuilt python3-reportlab/flask/gunicorn packages, because
# PyPI has no armv7 wheels for Pillow (a reportlab dependency) and compiling it
# under qemu is slow. The same image works on amd64 and arm64.
# poppler-utils provides pdftotext, which reads amounts from uploaded expense PDFs.
FROM debian:trixie-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-reportlab python3-flask gunicorn poppler-utils \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 app && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY src ./src
COPY config/sender.example.toml ./config/sender.example.toml
RUN printf '#!/bin/sh\nexec python3 -m invoices.cli "$@"\n' > /usr/local/bin/invoices \
 && chmod 755 /usr/local/bin/invoices \
 && mkdir -p /data /backups && chown app:app /data /backups

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INVOICES_DATA_DIR=/data \
    INVOICES_BACKUP_DIR=/backups \
    INVOICES_SENDER_FILE=/config/sender.toml

USER app
EXPOSE 8000
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

# One worker keeps memory low on the Pi and lets the login throttle live in memory.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "60", \
     "--access-logfile", "-", "invoices:create_app()"]

# Test stage: `docker build --target test .` runs the suite against the Debian packages.
FROM runtime AS test
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3-pytest && rm -rf /var/lib/apt/lists/*
COPY tests ./tests
COPY pyproject.toml ./
USER app
ENV HOME=/tmp
RUN python3 -m pytest -q -p no:cacheprovider
