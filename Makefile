# Contract: fill in target bodies on competition day, but never rename these targets.
# The CI workflow calls them by name.

.PHONY: install lint test build licences ci slice

-include make/*.mk

PYTHON ?= .venv/bin/python

install:
	@test -x "$(PYTHON)" || python3 -m venv .venv
	@for manifest in $$(find . -name requirements.txt -not -path './.venv/*' -not -path './.git/*'); do \
	       echo "install: pip install -r $$manifest"; \
	       $(PYTHON) -m pip install --disable-pip-version-check --quiet -r "$$manifest" || exit 1; \
	     done
	@printf '%s\n' 'install: dependencies installed from every requirements.txt'

lint:
	@$(PYTHON) -m compileall -q detector investigation tests stubs \
	  && printf '%s\n' 'lint: all Python sources compile'

test:
	@$(PYTHON) -m unittest discover -s tests -t .

build:
	@printf '%s\n' 'build: nothing to compile; the detector runs from source'

licences:
	@python3 scripts/licences.py

slice:
	@$(PYTHON) stubs/slice.py

ci: install lint test build licences
