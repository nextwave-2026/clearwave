# W2 detection - owner: andres

.PHONY: test-detector seed detect consume live

test-detector:
	@python3 -m unittest tests.test_detector tests.test_evidence tests.test_mappers tests.test_consumer

seed:
	@python3 -m detector seed

detect:
	@python3 -m detector detect

# Live path. Needs the broker from docker-compose.yml and one of raul's workers
# running; `make seed detect` is the same demonstration with no broker at all.
consume:
	@python3 -m detector consume

live:
	@python3 -m detector consume --seconds 60 --detect
