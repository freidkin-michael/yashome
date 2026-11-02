# The house TTS (docs/voice.md): Silero on the CPU. The models (CC BY-NC-SA 4.0, non-commercial) are not in the image:
# the service fetches them once into the volume at /models.
FROM python:3.12-slim
RUN pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir numpy==2.5.4 scipy
RUN mkdir -p /models /data && chmod 777 /models /data
WORKDIR /app
COPY service/tts/server.py service/tts/numwords.py ./
CMD ["python", "-u", "server.py"]
