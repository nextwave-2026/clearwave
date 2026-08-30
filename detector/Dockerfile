# Build from the repo root: docker build -f detector/Dockerfile .
# Isolation: this image must not gain a read path to C6. It copies detector/
# and nothing else - no worker/, no evaluator/, no state/ - so the only way
# ground truth could reach this process is a mount, which is why the compose
# service masks state/ground_truth/ over the shared ./state bind.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Only the Kafka client. The rest of the detection plane is standard library,
# which is what keeps the broker-free demo fallback broker-free.
COPY detector/requirements.txt detector/requirements.txt
RUN pip install --no-cache-dir -r detector/requirements.txt

COPY detector/ detector/

ENTRYPOINT ["python", "-m", "detector"]
CMD ["daemon"]
