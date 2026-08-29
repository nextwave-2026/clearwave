# W2 detection - owner: andres

.PHONY: test-detector seed detect

test-detector:
	@python3 -m unittest tests.test_detector tests.test_evidence

seed:
	@python3 -m detector seed

detect:
	@python3 -m detector detect
