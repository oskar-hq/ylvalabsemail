FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY templates ./templates
COPY email_templates ./email_templates
COPY static ./static

RUN useradd --system --uid 1000 mailer && mkdir -p /data && chown mailer /data
USER mailer
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

# Genau EIN Worker: Die Anmeldungen liegen im Arbeitsspeicher dieses Prozesses.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "600", "--bind", "0.0.0.0:8080", "--access-logfile", "-", "app.main:app"]
