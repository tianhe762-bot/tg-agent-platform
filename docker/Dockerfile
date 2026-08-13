FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TG_AGENT_ROOT=/opt/tg-agent

WORKDIR /opt/tg-agent

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY configs ./configs
COPY VERSION .

RUN mkdir -p data logs

VOLUME ["/opt/tg-agent/data", "/opt/tg-agent/logs", "/opt/tg-agent/configs"]

CMD ["python", "-m", "src.gateway"]
