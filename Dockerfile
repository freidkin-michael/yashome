FROM python:3.12-slim
WORKDIR /app

# Install python deps only. Application code is bind-mounted from host at runtime
# (docker-compose.yml: . -> /app). This means: edit files in the checkout, then
# `docker compose restart home` -- no rebuild needed unless requirements.txt changes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# HOME only has to be writable (library caches); nothing is kept there.
ENV HOME=/tmp

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/').read()" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
