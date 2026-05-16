"""Evaluators — one module per scoring strategy.

EVAL-B lands `judge` (G-Eval LLM-as-Judge). v0.2 will add `ragas`, `bertscore`,
and `pii` here; each exposes a pure `evaluate(prompt, completion, ...) -> Result`
function and the Celery task dispatches across them.
"""
