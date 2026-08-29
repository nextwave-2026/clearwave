# W3 investigation - owner: derek

.PHONY: test-investigation evaluate

test-investigation:
	@python3 -m unittest tests.test_investigation

evaluate:
	@python3 evaluator/test_score.py
