FROM python:3.12-slim

WORKDIR /srv
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app/src /srv/src
COPY app/templates /srv/templates
COPY app/static /srv/static

ENV PYTHONPATH=/srv/src
ENV CORVO_DATA=/srv/data
ENV CORVO_DB=/srv/db/corvo.db

RUN mkdir -p /srv/db /srv/data
COPY app/seed/corvo.db /srv/db/corvo.db
EXPOSE 8000
CMD ["sh", "-c", "uvicorn corvo.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
