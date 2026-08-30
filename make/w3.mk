# W3 investigation - owner: derek

.PHONY: test-investigation evaluate vertical-path investigate

test-investigation:
	@$(PYTHON) -m unittest tests.test_investigation tests.test_agent_loop tests.test_vertical_path tests.test_env

evaluate:
	@$(PYTHON) evaluator/test_score.py

vertical-path:
	@$(PYTHON) -m investigation.vertical

# Investigate an incident detection ALREADY stored - a live Kafka run included -
# without reseeding it. `make vertical-path` is the offline seed+detect+investigate
# demonstration; this is the join onto a prepared store.
#   make investigate DB=/tmp/clearwave-live.db
#   make investigate DB=/tmp/clearwave-live.db INCIDENT=inc-2026-08-30-715ab9c3
investigate:
	@$(PYTHON) -m investigation.vertical --investigate-only \
	  $(if $(DB),--db "$(DB)") $(if $(INCIDENT),--incident-id "$(INCIDENT)")
