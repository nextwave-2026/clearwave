"""W2 detection plane: deterministic measurement, detection and prioritisation.

No module in this package may call a language model. Severity is a business
function, diagnosis is not ours, and every number this package produces must be
reproducible from the same input.
"""

__all__ = ["config", "mappers", "schema", "store", "metrics", "detect", "evidence"]
