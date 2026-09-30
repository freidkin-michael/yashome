FROM python:3.12-slim
WORKDIR /app

# Install python deps only. Application code is bind-mounted from host at runtime
# (docker-compose.yml: . -> /app). This means: edit files in the checkout, then
# `docker compose restart home` -- no rebuild needed unless requirements.txt changes.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Python packages your plug-ins need (EXTRA_PIP in .env, e.g. "cryptography==50.0.1"); make update rebuilds
# pinned names only (name==version), no pip options; nothing already installed can change
ARG EXTRA_PIP=""
RUN if [ -n "$EXTRA_PIP" ]; then \
      for p in $EXTRA_PIP; do echo "$p" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9_.-]*(\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.!+-]+$' \
        || { echo "EXTRA_PIP: '$p' is not name==version"; exit 1; }; done; \
      pip freeze > /tmp/constraints.txt; \
      pip install --no-cache-dir -c /tmp/constraints.txt $EXTRA_PIP; \
    fi

# HOME only has to be writable (library caches); nothing is kept there.
ENV HOME=/tmp

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/').read()" || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8765"]
