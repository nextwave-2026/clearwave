# Contract: fill in target bodies on competition day, but never rename these targets.
# The CI workflow calls them by name.

.PHONY: install lint test build licences ci slice evaluate

install:
	@python3 -c 'import sys; assert sys.version_info >= (3, 11), sys.version' \
	  && printf '%s\n' 'install: standard library only, no third-party dependencies to install'

lint:
	@python3 -m compileall -q detector tests stubs \
	  && printf '%s\n' 'lint: all Python sources compile'

test:
	@python3 -m unittest discover -s tests -t .

build:
	@printf '%s\n' 'build: nothing to compile; the detector runs from source'

licences:
	@python3 scripts/licences.py

slice:
	@python3 stubs/slice.py

evaluate:
	@python3 evaluator/test_score.py

ci: install lint test build licences
