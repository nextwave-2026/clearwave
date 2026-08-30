# Shared live-stack targets. Not a workstream fragment: w1.mk, w2.mk and w4.mk
# stay with their owners. Do not rename install/lint/test/build/licences/ci.

STACK_SURFACES_HOST_PORT ?= 8082
STACK_SURFACES_URL ?= http://127.0.0.1:$(STACK_SURFACES_HOST_PORT)
STACK_WAIT_TIMEOUT ?= 300
PREPARE_HOURS ?= 8
PREPARE_SEED ?= 20260830

.PHONY: stack-up stack-status stack-down stack-prepare verify-demo

stack-prepare:
	@mkdir -p state
	@python3 -S scripts/prepare_history.py --hours $(PREPARE_HOURS) --seed $(PREPARE_SEED)

stack-up: stack-prepare
	@docker compose up -d --build --wait --wait-timeout $(STACK_WAIT_TIMEOUT) \
	  kafka schema-registry \
	  worker-merchant-a worker-merchant-b worker-merchant-c \
	  detector investigation surfaces
	@printf '%s\n' "Open $(STACK_SURFACES_URL)/"
	@printf '%s\n' "Store already holds healthy merchant-b/adyen history (see prepare lines above). Live traffic stitches onto it; the anomaly still happens after the judge presses the button."

stack-status:
	@python3 -S scripts/stack_status.py

stack-down:
	@docker compose down

# Isolated from a live demo on 8082/9092. Extra args: make verify-demo ARGS='--keep-stack'
verify-demo:
	@python3 -S scripts/verify_demo.py $(ARGS)
