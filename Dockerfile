FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd -r -u 1000 appuser && \
    chown -R appuser:appuser /app && \
    touch /app/failed_leads.log && \
    chown appuser:appuser /app/failed_leads.log && \
    chmod 644 /app/failed_leads.log

USER appuser

CMD ["bash", "entrypoint.sh"]
