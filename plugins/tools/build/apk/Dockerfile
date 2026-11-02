# The app builder: the build server's own code in its "apk" mode on the Android SDK image (pinned).
FROM cimg/android:2024.07@sha256:404e097f5191bd7d06bf70a1586ca823c9729b640e96e0a800176999e5ad9904
USER root
RUN mkdir -p /srv /work /cache && chown circleci /work /cache
COPY server/server.py /srv/server.py
USER circleci
ENV BUILD_MODE=apk BUILD_WORK=/work GRADLE_USER_HOME=/cache/gradle PYTHONUNBUFFERED=1
CMD ["python3", "-u", "/srv/server.py"]
