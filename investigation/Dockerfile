# Build from the repo root: docker build -f investigation/Dockerfile .
# Isolation: this image must not gain a read path to C6.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY detector/ detector/
COPY investigation/ investigation/
COPY stubs/ stubs/

ENTRYPOINT ["python", "-m", "investigation"]
CMD []
