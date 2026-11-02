FROM python:3.12-slim
WORKDIR /app
COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bridge/yash_bridge.py bridge/database.rules.json i18n.json ./
USER 1000:1000
CMD ["python", "-u", "yash_bridge.py", "serve"]
