#!/usr/bin/env python3
"""Collect repeatable platform-only performance evidence.

The workload is intentionally domain-neutral: a small configuration plugin
run exercises discovery, inspection, decision, commit, ledger construction,
and performance-bill generation without a browser or Agent transport.  The
result is evidence, not a product latency SLO; unavailable external telemetry
is left unavailable by the platform bill.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assayer_platform import (
    PlatformContext,
    PlatformKernel,
    build_platform_performance_bill,
)
from tests.helpers.config_quality import ConfigQualityPlugin, ConfigurationDecisionProvider


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def run(iterations: int = 10) -> dict[str, Any]:
    if not isinstance(iterations, int) or iterations < 3:
        raise ValueError("iterations must be at least 3")
    durations_ms: list[float] = []
    bills: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="assayer-performance-") as directory:
        source = Path(directory) / "settings.json"
        source.write_text(json.dumps({"name": "performance", "enabled": True}), encoding="utf-8")
        for index in range(iterations):
            started = time.perf_counter_ns()
            result = PlatformKernel().run(
                ConfigQualityPlugin(),
                str(source),
                "CFG-001",
                ConfigurationDecisionProvider(),
                PlatformContext(f"performance-evidence-{index}", frozenset({"structured_read"})),
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            if result.status != "completed" or result.ledger is None:
                raise RuntimeError(f"performance workload did not complete: {result.status}")
            bill = build_platform_performance_bill(result.ledger)
            if bill["measurement"]["wallClock"]["status"] != "captured":
                raise RuntimeError("platform wall-clock telemetry was not captured")
            durations_ms.append(round(elapsed_ms, 3))
            bills.append({
                "runId": result.run_id,
                "status": result.status,
                "wallClockMs": bill["measurement"]["wallClock"]["durationMs"],
                "hostOperationMs": bill["measurement"]["hostOperations"]["durationMs"],
                "operations": bill["source"]["operationCount"],
            })
    return {
        "schemaVersion": "1.0.0",
        "status": "passed",
        "workload": {
            "plugin": "config-quality-fixture",
            "check": "CFG-001",
            "iterations": iterations,
            "browser": False,
            "agentTransport": False,
        },
        "elapsedMs": {
            "min": round(min(durations_ms), 3),
            "max": round(max(durations_ms), 3),
            "mean": round(statistics.mean(durations_ms), 3),
            "p50": round(_percentile(durations_ms, 0.50), 3),
            "p95": round(_percentile(durations_ms, 0.95), 3),
        },
        "runs": bills,
        "limitations": [
            "This is a deterministic platform workload, not a browser or Agent end-to-end journey.",
            "Agent wait, model, and transport telemetry remain not_exposed by design.",
            "The numbers are a reproducible baseline, not a universal latency SLO.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()
    print(json.dumps(run(args.iterations), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
