# Contract: fill in target bodies on competition day, but never rename these targets.
# The CI workflow calls them by name.

.PHONY: install lint test build licences ci slice evaluate test-investigation

PYTHON ?= .venv/bin/python

install:
	@test -x "$(PYTHON)" || python3 -m venv .venv
	@$(PYTHON) -m pip install --disable-pip-version-check --requirement requirements.txt

lint:
	@$(PYTHON) -m compileall -q detector investigation tests stubs \
	  && printf '%s\n' 'lint: all Python sources compile'

test:
	@$(PYTHON) -m unittest discover -s tests -t .

test-investigation:
	@$(PYTHON) -m unittest tests.test_investigation tests.test_agent_loop

build:
	@printf '%s\n' 'build: nothing to compile; the detector runs from source'

licences:
	@python3 scripts/licences.py

slice:
	@$(PYTHON) stubs/slice.py

evaluate:
	@$(PYTHON) evaluator/test_score.py

ci: install lint test build licences
