"""Benchmark: compare Claude Code (single agent) vs openMax (multi-agent) completion times."""

from openmax.benchmark.runner import BenchmarkResult, run_benchmark
from openmax.benchmark.tasks import BenchmarkTask, load_task, load_task_suite

__all__ = ["BenchmarkResult", "BenchmarkTask", "load_task", "load_task_suite", "run_benchmark"]
