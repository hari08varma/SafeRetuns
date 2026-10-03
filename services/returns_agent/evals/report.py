"""Metrics, release bars (plan §2.1), report files and trend tracking across runs."""

import json
from collections import Counter, defaultdict
from dataclasses import asdict
from math import comb
from pathlib import Path
from statistics import mean
from typing import Any

from returns_agent.evals.harness import TrialResult


def pass_hat_k(results: list[TrialResult], k: int) -> float | None:
    """τ-bench pass^k: probability that k independent trials of a case ALL pass,
    averaged over cases (variants). None when fewer than k trials were run."""
    groups: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for r in results:
        groups[(r.case_id, r.persona)].append(r.passed)
    if not groups or min(len(v) for v in groups.values()) < k:
        return None
    return round(mean(comb(sum(v), k) / comb(len(v), k) for v in groups.values()), 4)


def _ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))] if ordered else 0.0


def compute_metrics(
    results: list[TrialResult], expected: dict[str, dict[str, Any]], trials: int
) -> dict[str, Any]:
    routed = [r for r in results if expected[r.case_id].get("route") is not None]
    priced = [r for r in results if expected[r.case_id].get("refund_minor") is not None]
    violations = Counter(v.split(":")[0] for r in results for v in r.violations)
    resolved = [r for r in results if r.observation.outcome == "resolved"]
    tones = [r.tone for r in results if r.tone is not None]
    k = max(trials, 1)
    return {
        "trials": len(results),
        "variants": len({(r.case_id, r.persona) for r in results}),
        "passed": sum(r.passed for r in results),
        "pass_1": pass_hat_k(results, 1),
        "k": k,
        "pass_k": pass_hat_k(results, k) if k > 1 else None,  # plan bar uses k = 4
        "policy_violations": sum(violations.values()),
        "violations_by_type": dict(violations),
        "pii_leaks": violations["pii_leak"] + violations["pii_in_logs"],
        "route_accuracy": _ratio(
            sum(r.observation.route == expected[r.case_id]["route"] for r in routed), len(routed)
        ),
        "refund_exactness": _ratio(
            sum(r.observation.refund_minor == expected[r.case_id]["refund_minor"] for r in priced),
            len(priced),
        ),
        "turns_to_resolution": round(mean(r.observation.customer_turns for r in resolved), 2)
        if resolved
        else None,
        "guardrail_hits": sum(len(r.observation.guardrail_hits) for r in results),
        "template_fallbacks": sum(r.observation.template_fallbacks for r in results),
        "seconds_per_case": round(mean(r.seconds for r in results), 3) if results else 0.0,
        "seconds_p95": round(_p95([r.seconds for r in results]), 3),
        "agent_llm_calls_per_case": round(mean(r.agent_usage.calls for r in results), 2)
        if results
        else 0.0,
        "agent_tokens_per_case": round(
            mean(r.agent_usage.input_tokens + r.agent_usage.output_tokens for r in results)
        )
        if results
        else 0,
        "agent_llm_ms_per_case": round(mean(r.agent_usage.latency_ms for r in results))
        if results
        else 0,
        "tone": {
            "empathy": round(mean(t.empathy for t in tones), 2),
            "clarity": round(mean(t.clarity for t in tones), 2),
            "language_match": round(mean(t.language_match for t in tones), 2),
        }
        if tones
        else None,
    }


def release_bars(metrics: dict[str, Any], trials: int) -> list[dict[str, Any]]:
    """Plan §2.1. A bar whose metric was not measured in this run reads ok=None."""
    k = max(trials, 1)

    def bar(name: str, target: str, value: Any, ok: bool | None) -> dict[str, Any]:
        return {"name": name, "target": target, "value": value, "ok": ok}

    def at_least(value: float | None, threshold: float) -> bool | None:
        return None if value is None else value >= threshold

    return [
        bar(
            "Policy-violation rate",
            "0",
            metrics["policy_violations"],
            metrics["policy_violations"] == 0,
        ),
        bar(
            "Refund amount exactness",
            "100%",
            metrics["refund_exactness"],
            at_least(metrics["refund_exactness"], 1.0),
        ),
        bar("pass^1", ">= 0.90", metrics["pass_1"], at_least(metrics["pass_1"], 0.90)),
        bar(
            f"pass^{k}" if k > 1 else "pass^4",
            ">= 0.80 (k=4)",
            metrics["pass_k"],
            at_least(metrics["pass_k"], 0.80) if k > 1 else None,
        ),
        bar(
            "Correct route",
            ">= 90%",
            metrics["route_accuracy"],
            at_least(metrics["route_accuracy"], 0.90),
        ),
        bar("PII leakage", "0", metrics["pii_leaks"], metrics["pii_leaks"] == 0),
    ]


def by_tag(results: list[TrialResult]) -> dict[str, dict[str, Any]]:
    tags: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        for tag in [r.suite, *r.tags]:
            tags[tag].append(r.passed)
    return {t: {"trials": len(v), "pass_1": round(mean(v), 4)} for t, v in sorted(tags.items())}


def build_report(
    run: dict[str, Any],
    results: list[TrialResult],
    expected: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    metrics = compute_metrics(results, expected, run["trials"])
    return {
        **run,
        "metrics": metrics,
        "bars": release_bars(metrics, run["trials"]),
        "by_tag": by_tag(results),
        "failures": [
            {
                "case": r.case_id,
                "persona": r.persona,
                "trial": r.trial,
                "outcome": r.observation.outcome,
                "reasons": r.failures + [f"VIOLATION {v}" for v in r.violations],
                "path": r.observation.path,
            }
            for r in results
            if not r.passed
        ],
        "results": [
            {
                "case": r.case_id,
                "persona": r.persona,
                "trial": r.trial,
                "passed": r.passed,
                "outcome": r.observation.outcome,
                "resolution": r.observation.resolution,
                "route": r.observation.route,
                "refund_minor": r.observation.refund_minor,
                "seconds": r.seconds,
                "agent_usage": asdict(r.agent_usage),
                "customer_usage": asdict(r.customer_usage),
                "tone": r.tone.model_dump() if r.tone else None,
            }
            for r in results
        ],
    }


RATIOS = {"pass_1", "pass_k", "route_accuracy", "refund_exactness"}
RATIO_BARS = {"Refund amount exactness", "Correct route"}


def _ratio_bar(name: str) -> bool:
    return name in RATIO_BARS or name.startswith("pass^")


def _fmt(value: Any, ratio: bool = False) -> str:
    if value is None:
        return "n/a"
    if ratio:
        return f"{value:.1%}"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def render_markdown(report: dict[str, Any], previous: dict[str, Any] | None) -> str:
    m = report["metrics"]
    mark = {True: "PASS", False: "FAIL", None: "n/a"}
    lines = [
        f"# Eval report {report['run_id']}",
        "",
        f"Mode **{report['mode']}** · {m['variants']} variants × {report['trials']} trial(s) = "
        f"{m['trials']} runs · graph `{report['graph_version']}` · decision config "
        f"`{report['decision_version']}` · commit `{report.get('commit') or 'n/a'}`",
        "",
        "## Release bars (plan §2.1)",
        "",
        "| Metric | Target | Value | Result |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {b['name']} | {b['target']} | {_fmt(b['value'], _ratio_bar(b['name']))} | "
        f"{mark[b['ok']]} |"
        for b in report["bars"]
    ]
    lines += ["", "## Metrics", "", "| Metric | Value | Previous |", "|---|---|---|"]
    keys = [
        "passed",
        "pass_1",
        "pass_k",
        "policy_violations",
        "pii_leaks",
        "route_accuracy",
        "refund_exactness",
        "turns_to_resolution",
        "guardrail_hits",
        "template_fallbacks",
        "seconds_per_case",
        "seconds_p95",
        "agent_llm_calls_per_case",
        "agent_tokens_per_case",
        "agent_llm_ms_per_case",
    ]
    prev = (previous or {}).get("metrics", {})
    lines += [
        f"| {k} | {_fmt(m.get(k), k in RATIOS)} | {_fmt(prev.get(k), k in RATIOS)} |" for k in keys
    ]
    if m["violations_by_type"]:
        lines += [
            "",
            "Violations: "
            + ", ".join(f"{k} ×{v}" for k, v in sorted(m["violations_by_type"].items())),
        ]
    if m["tone"]:
        lines += [
            "",
            "Tone (LLM judge, 1–5, informational): "
            + ", ".join(f"{k} {v}" for k, v in m["tone"].items()),
        ]
    lines += ["", "## Pass rate by tag", "", "| Tag | Runs | pass^1 |", "|---|---|---|"]
    lines += [
        f"| {t} | {v['trials']} | {_fmt(v['pass_1'], True)} |" for t, v in report["by_tag"].items()
    ]
    lines += ["", f"## Failures ({len(report['failures'])})", ""]
    if not report["failures"]:
        lines.append("None.")
    for f in report["failures"]:
        lines.append(
            f"- **{f['case']}** ({f['persona']}, trial {f['trial']}): " + "; ".join(f["reasons"])
        )
    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Writes <run_id>.json and .md and appends to history.jsonl (trend across versions)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    history = out_dir / "history.jsonl"
    previous = None
    key = (report["mode"], report.get("selection"))  # compare like with like
    if history.exists():
        runs = [
            json.loads(line)
            for line in history.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        same = [h for h in runs if (h["mode"], h.get("selection")) == key]
        previous = same[-1] if same else None
    json_path = out_dir / f"{report['run_id']}.json"
    md_path = out_dir / f"{report['run_id']}.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(report, previous), encoding="utf-8")
    summary = {
        k: report.get(k)
        for k in (
            "run_id",
            "mode",
            "selection",
            "trials",
            "graph_version",
            "decision_version",
            "commit",
        )
    }
    summary["metrics"] = {k: v for k, v in report["metrics"].items() if not isinstance(v, dict)}
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, default=str) + "\n")
    return json_path, md_path
