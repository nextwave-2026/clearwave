# W3 investigation - owner: derek

.PHONY: test-investigation evaluate

test-investigation:
	@$(PYTHON) -m unittest tests.test_investigation tests.test_agent_loop

evaluate:
	@$(PYTHON) evaluator/test_score.py
