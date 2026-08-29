# Contract: fill in target bodies on competition day, but never rename these targets.
# The CI workflow calls them by name.

.PHONY: install lint test build licences ci slice evaluate test-investigation

install:
	@python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
	  && for manifest in $$(find . -name requirements.txt -not -path './.git/*'); do \
	       echo "install: pip install -r $$manifest"; \
	       python3 -m pip install --quiet -r "$$manifest" || exit 1; \
	     done \
	  && printf '%s\n' 'install: dependencies installed from every requirements.txt'

lint:
	@python3 -m compileall -q detector investigation tests stubs \
	  && printf '%s\n' 'lint: all Python sources compile'

test:
	@python3 -m unittest discover -s tests -t .

test-investigation:
	@python3 -m unittest tests.test_investigation

build:
	@printf '%s\n' 'build: nothing to compile; the detector runs from source'

licences:
	@python3 scripts/licences.py

slice:
	@python3 stubs/slice.py

evaluate:
	@python3 evaluator/test_score.py

ci: install lint test build licences
