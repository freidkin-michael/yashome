# Yashome helper: small privileged API for the dashboard (plug-ins, .env keys,
# apply / update / rollback). The same image runs the one-shot job container.
# No secrets here: HELPER_KEY and the checkout come in at run time.
# Pinned: this image holds the docker socket. Bump the digest on purpose (docker pull, image inspect).
FROM python:3.12-alpine@sha256:1b668429b3511ab407d8e00648891631b0b1a4d7e15e3ca70f38ab5b91ad4ab4

RUN apk add --no-cache docker-cli docker-cli-compose git make openssh-client openssl

# ssh refuses a uid without a passwd entry; the helper and its jobs run as PUID:PGID
ARG PUID=1000
ARG PGID=1000
RUN grep -q "^[^:]*:[^:]*:$PUID:" /etc/passwd || echo "yashome:x:$PUID:$PGID::/tmp:/sbin/nologin" >> /etc/passwd

ENV PYTHONDONTWRITEBYTECODE=1
COPY helper.py host_probe.sh /helper/

CMD ["python3", "-u", "/helper/helper.py"]
