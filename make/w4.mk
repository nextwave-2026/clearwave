# W4 surfaces - owner: juank

.PHONY: test-surfaces surfaces-serve surfaces-lint check-phone-dry

lint: surfaces-lint

surfaces-lint:
	@$(PYTHON) -m compileall -q surfaces

test-surfaces:
	@$(PYTHON) -m unittest tests.test_surfaces

surfaces-serve:
	@$(PYTHON) -m surfaces

check-phone-dry:
	@$(PYTHON) scripts/check_phone_channel.py --dry-run
