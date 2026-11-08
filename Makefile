.PHONY: test validate demo benchmark

test:
	python3 -m pytest

validate:
	PYTHONPATH=src python3 -m agent_fleet_controller.cli validate examples/demo-trace.jsonl

demo:
	PYTHONPATH=src python3 -m agent_fleet_controller.cli simulate examples/demo-trace.jsonl --pretty

benchmark:
	mkdir -p validation-results
	PYTHONPATH=src python3 -m agent_fleet_controller.cli generate --agents 200 --seed 20261108 --output validation-results/synthetic-200.jsonl
	PYTHONPATH=src python3 -m agent_fleet_controller.cli simulate validation-results/synthetic-200.jsonl --cpu-hourly 0.05 --memory-gib-hourly 0.006 --pretty > validation-results/synthetic-200-result.json
