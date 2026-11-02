FROM esphome/esphome:2026.7.4
WORKDIR /srv
COPY server.py ./
ENV PLATFORMIO_CORE_DIR=/cache ESPHOME_ESP_IDF_PREFIX=/cache/idf ESPHOME_SDK_NRF_PREFIX=/cache/sdk-nrf \
    BUILD_WORK=/work PYTHONUNBUFFERED=1
ENTRYPOINT []
# The base image probes the ESPHome dashboard port (6052), which this server does not serve; any HTTP answer on its own port is alive (401 = key needed).
HEALTHCHECK --interval=30s --timeout=5s CMD curl -s -o /dev/null http://localhost:8795/v1/info || exit 1
CMD ["python3", "-u", "/srv/server.py"]
