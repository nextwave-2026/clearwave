# W3 investigation - owner: derek

.PHONY: test-investigation evaluate vertical-path investigate investigate-daemon

test-investigation:
	@$(PYTHON) -m unittest tests.test_investigation tests.test_agent_loop tests.test_vertical_path tests.test_env tests.test_investigation_daemon

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

# Continuous watcher over a store detection already wrote. Does not seed or
# detect. Stop with Ctrl-C or SIGTERM; both drain in-flight investigations.
#   make investigate-daemon
#   make investigate-daemon DB=state/clearwave.db POLL=2 MAX_POLLS=5
investigate-daemon:
	@PYTHONUNBUFFERED=1 $(PYTHON) -m investigation \
	  $(if $(DB),--db "$(DB)") \
	  $(if $(POLL),--poll-interval "$(POLL)") \
	  $(if $(MAX_POLLS),--max-polls "$(MAX_POLLS)")
