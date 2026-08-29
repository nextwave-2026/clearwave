# Build from the repo root: docker build -f worker/Dockerfile .
# One image, one process per merchant - which merchant is the only thing
# that varies between containers. ENTRYPOINT is fixed to the worker
# module; CMD is deliberately empty rather than a baked-in "merchant-a" -
# a non-empty default CMD is an explicit argv value and always wins over
# worker.py's MERCHANT_TYPE env-var fallback, silently ignoring the env
# var even when it's set (verified: with CMD ["merchant-a"], `docker run
# -e MERCHANT_TYPE=merchant-b <image>` still runs as merchant-a). With CMD
# empty, both paths work:
#
#   docker run -e MERCHANT_TYPE=merchant-b <image>
#   docker run <image> merchant-b --incident-provider adyen --incident-effect outage
#
# KAFKA_BOOTSTRAP_SERVERS and SCHEMA_REGISTRY_URL are not baked in here -
# supply them from whatever runs the container (docker-compose
# environment:), same as the devspace service does.
FROM python:3.12-slim

# unbuffered stdout - otherwise `docker logs` shows nothing for a long time
# because print() block-buffers when not attached to a TTY
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY worker/requirements.txt worker/requirements.txt
RUN pip install --no-cache-dir -r worker/requirements.txt

COPY worker/ worker/

ENTRYPOINT ["python", "-m", "worker.worker"]
CMD []
