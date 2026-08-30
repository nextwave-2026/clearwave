# W2 detection - owner: andres

.PHONY: test-detector seed detect consume live detect-daemon backfill e2e

test-detector:
	@python3 -m unittest tests.test_detector tests.test_evidence tests.test_mappers tests.test_consumer

seed:
	@python3 -m detector seed

detect:
	@python3 -m detector detect

# Live path. Needs the broker from docker-compose.yml and one of raul's workers
# running; `make seed detect` is the same demonstration with no broker at all.
# These two run on the venv, not bare python3: the consumer is the only part of
# W2 that needs confluent-kafka, and `make install` is what puts it there.
consume:
	@$(PYTHON) -m detector consume

# Sweep while consuming, not only at the end: a watch on a developing
# deviation is worth nothing if it first appears after the cliff.
DETECT_EVERY ?= 45

live:
	@$(PYTHON) -m detector consume --seconds 60 --detect --detect-every $(DETECT_EVERY)

# The same consume-and-sweep loop, run as a service rather than a command:
# empty polls are not terminal and only SIGINT or SIGTERM ends it, both
# draining the batch in flight. This is what the `detector` compose service
# runs; the target is here so a person can run it on the host too.
#   make detect-daemon
#   make detect-daemon DB=state/clearwave.db DETECT_EVERY=15
detect-daemon:
	@PYTHONUNBUFFERED=1 $(PYTHON) -m detector $(if $(DB),--db "$(DB)") daemon \
	  --detect-every $(DETECT_EVERY)

# W1's replayable history, streamed rather than held in memory. Point BACKFILL
# at the file; it is not in the repository, and at 83 MB it never will be.
backfill:
	@test -n "$(BACKFILL)" || { echo "set BACKFILL=/path/to/backfill.jsonl"; exit 1; }
	@$(PYTHON) -m detector ingest "$(BACKFILL)" --stream

# The whole live end-to-end of docs/live-ingestion.md in one command, for the
# person reproducing this at 03:00: raul's stack, the backfill if they have
# one, then live traffic through to a stored C3 record. Then `make
# surfaces-serve` and hit the judge toggle on the dashboard.
#
# Detection needs sustained event-time contrast, so this consumes for three
# minutes, not the sixty seconds `make live` uses. A shorter sweep after an
# injection reports `incident: null` and looks like a failure when it is only
# an unfinished run. Override with CONSUME_SECONDS.
CONSUME_SECONDS ?= 180

e2e:
	@docker compose up -d kafka schema-registry \
	  worker-merchant-a worker-merchant-b worker-merchant-c
	@test -z "$(BACKFILL)" || $(PYTHON) -m detector ingest "$(BACKFILL)" --stream
	@$(PYTHON) -m detector consume --seconds $(CONSUME_SECONDS) --detect --detect-every $(DETECT_EVERY)
