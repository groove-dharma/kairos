FROM python:3.12-slim

WORKDIR /app
COPY daemon_delta.py codex_template.json ./

ENV DELTA_HOME=/deltas \
    ES_URL=http://elasticsearch:9200 \
    DELTA_POLL=10 \
    PYTHONUNBUFFERED=1

# Sin requirements: el daemon usa solo biblioteca estandar.
CMD ["python3", "daemon_delta.py", "vigilar"]
