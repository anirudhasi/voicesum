"""
Golden dataset evaluation harness (W0.1).

Turns "the output looks better" into numbers that can be compared across
builds. Every quality claim about speaker identification, information retention
in condensed ROM versions and action-point extraction is meant to be backed by a
report produced here, not by inspection.

    python -m eval.runner --dataset eval/golden --out eval/reports/latest

See eval/README.md for the dataset format, the labelling workflow and the exact
definition of every metric.
"""
