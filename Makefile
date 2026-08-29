# Contract: fill in target bodies on competition day, but never rename these targets.
# The CI workflow calls them by name.

.PHONY: install lint test build licences slice evaluate ci

install:
	@printf '%s\n' 'install: unimplemented placeholder - fill in on competition day'

lint:
	@printf '%s\n' 'lint: unimplemented placeholder - fill in on competition day'

test:
	@printf '%s\n' 'test: unimplemented placeholder - fill in on competition day'

build:
	@printf '%s\n' 'build: unimplemented placeholder - fill in on competition day'

licences:
	@python3 scripts/licences.py

slice:
	@python3 stubs/slice.py

evaluate:
	@python3 evaluator/test_score.py

ci: install lint test build licences
