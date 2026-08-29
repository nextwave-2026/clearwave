# W4 surfaces - owner: juank

.PHONY: test-surfaces surfaces-serve surfaces-lint check-phone check-phone-dry

lint: surfaces-lint

surfaces-lint:
	@$(PYTHON) -m compileall -q surfaces

test-surfaces:
	@$(PYTHON) -m unittest tests.test_surfaces

surfaces-serve:
	@$(PYTHON) -m surfaces

# Places a REAL phone call. Reports Twilio's own error instead of swallowing it,
# which escalate() must do in production. Needs the CLEARWAVE_TWILIO_* variables.
check-phone:
	@$(PYTHON) scripts/check_phone_channel.py

check-phone-dry:
	@$(PYTHON) scripts/check_phone_channel.py --dry-run
