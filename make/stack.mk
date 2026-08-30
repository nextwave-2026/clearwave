# Shared live-stack targets. Not a workstream fragment: w1.mk, w2.mk and w4.mk
# stay with their owners. Do not rename install/lint/test/build/licences/ci.

STACK_SURFACES_HOST_PORT ?= 8082
STACK_SURFACES_URL ?= http://127.0.0.1:$(STACK_SURFACES_HOST_PORT)
STACK_WAIT_TIMEOUT ?= 300

.PHONY: stack-up stack-status stack-down

stack-up:
	@mkdir -p state
	@docker compose up -d --build --wait --wait-timeout $(STACK_WAIT_TIMEOUT) \
	  kafka schema-registry \
	  worker-merchant-a worker-merchant-b worker-merchant-c \
	  detector investigation surfaces
	@printf '%s\n' "Open $(STACK_SURFACES_URL)/"
	@printf '%s\n' "Leave this running. Merchant-relative detection compares against a 60-minute trailing window (BASELINE_TRAILING_BUCKETS=60); a stack started in the last hour is still cold."

stack-status:
	@python3 -S scripts/stack_status.py

stack-down:
	@docker compose down
