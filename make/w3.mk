# W3 investigation - owner: derek

.PHONY: test-investigation evaluate vertical-path

test-investigation:
	@$(PYTHON) -m unittest tests.test_investigation tests.test_agent_loop tests.test_vertical_path

evaluate:
	@$(PYTHON) evaluator/test_score.py

vertical-path:
	@$(PYTHON) -m investigation.vertical
