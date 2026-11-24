# One image for both programs. Standard library only, so nothing is installed with pip.
FROM python:3.12-slim

# tzdata: the statistics use the local time of the host, and the time zone comes from TZ.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin hw \
    && mkdir /app /data \
    && chown hw:hw /data

WORKDIR /app
COPY --chown=hw:hw config.py collector.py api_server.py notify_signal.py tariff_mod.py \
     pq_data.py pq_page.py alarm_imbalance.py alarm_phase_amp.py alarm_spike.py \
     alarm_stale.py alarm_voltage.py ui.html ./

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HW_DB_PATH=/data/homewizard.db
VOLUME /data
USER hw

# The command is set per service in docker-compose.yml.
CMD ["python", "collector.py"]
