# The voice container (README, "One voice container"): the TTS (Silero on the CPU) and the recognizer service (webrtcvad,
# Vosk, the house's phrase map, the model fallback). No model is in the image: the Silero models (CC BY-NC-SA 4.0,
# non-commercial) and the Vosk model are fetched once into the volumes at /models and /vosk.
FROM python:3.12-slim
RUN pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir numpy==2.5.4 scipy
RUN pip install --no-cache-dir vosk==0.3.45 webrtcvad-wheels==2.0.14.post1 paho-mqtt==1.6.1 requests==2.34.2
RUN mkdir -p /models /vosk /data /house && chmod 777 /models /vosk /data
ENV PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY service/run.py ./
COPY service/tts/server.py service/tts/numwords.py ./tts/
COPY service/vad/*.py ./vad/
CMD ["python", "-u", "run.py"]
