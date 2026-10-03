"""Run the eval suite and write a report.

  python -m returns_agent.evals.run --mode smoke            # no model: scripted customers
  python -m returns_agent.evals.run --mode full --trials 4  # DeepSeek agent + LLM customers

Smoke runs in CI on every change; full runs nightly and before a release. The database must
be migrated, and its name must end in _test or _eval (the harness adds customers and orders).
"""

import argparse
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from returns_agent.config import config_dir, get_settings
from returns_agent.db.models import Product
from returns_agent.db.session import get_engine
from returns_agent.decision.config import load_decision_config
from returns_agent.evals.cases import EvalCase, Mode, evals_dir, load_cases
from returns_agent.evals.harness import Harness, TrialResult, open_harness
from returns_agent.evals.report import build_report, write_report
from returns_agent.llm.factory import build_llm_client
from returns_agent.seed.generator import generate


def ensure_catalog(session: Session) -> None:
    """Products the cases refer to (idempotent)."""
    known = set(session.scalars(select(Product.sku)))
    session.add_all(
        Product(
            sku=p.sku,
            title=p.title,
            category=p.category,
            variant=p.variant,
            price_minor=p.price_minor,
            returnable=p.returnable,
            image_uris=[p.image_uri],
        )
        for p in generate(seed=1, customers=1).products
        if p.sku not in known
    )
    session.commit()


def run_suite(
    harness: Harness,
    cases: list[EvalCase],
    mode: Mode,
    trials: int,
    progress: bool = False,
) -> list[TrialResult]:
    results = []
    for case in cases:
        for persona in case.variants(mode):
            for trial in range(1, trials + 1):
                result = harness.run(case, persona, trial)
                results.append(result)
                if progress:
                    status = "ok  " if result.passed else "FAIL"
                    print(
                        f"{status} {case.id} [{persona} #{trial}] {result.seconds:.1f}s", flush=True
                    )
    return results


def _commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    parser.add_argument("--trials", type=int, help="trials per case (smoke 1, full 4)")
    parser.add_argument("--suite", choices=["cases", "redteam", "all"], default="all")
    parser.add_argument("--tag", action="append", default=[], help="only cases with this tag")
    parser.add_argument("--case", action="append", default=[], help="only this case id")
    parser.add_argument("--out", type=Path, default=None, help="report directory")
    parser.add_argument("--check-bars", action="store_true", help="exit 1 if a bar fails")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any trial fails")
    args = parser.parse_args(argv)

    settings = get_settings()
    db_name = settings.database_url.rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith(("_test", "_eval")):
        print(f"refusing to write eval data to '{db_name}': use a *_test or *_eval database")
        return 2
    mode: Mode = "scripted" if args.mode == "smoke" else "llm"
    trials = args.trials or (1 if mode == "scripted" else 4)
    agent_llm = customer_llm = judge_llm = None
    if mode == "llm":
        agent_llm = build_llm_client(settings)
        customer_llm = build_llm_client(settings)
        judge_llm = build_llm_client(settings)
        if agent_llm is None:
            print("full mode needs a model: set LLM_PROVIDER=deepseek and DEEPSEEK_API_KEY")
            return 2

    suites = ("cases", "redteam") if args.suite == "all" else (args.suite,)
    cases = [
        c
        for c in load_cases(suites=suites)
        if (not args.tag or set(args.tag) & set(c.tags))
        and (not args.case or c.id in args.case)
        and c.variants(mode)
    ]
    engine = get_engine()
    with Session(engine) as session:
        ensure_catalog(session)
    started = datetime.now(UTC)
    with open_harness(settings.database_url, engine, agent_llm, customer_llm, judge_llm) as h:
        results = run_suite(h, cases, mode, trials, progress=True)

    run: dict[str, Any] = {
        "run_id": f"{started:%Y%m%dT%H%M%SZ}-{args.mode}",
        "started_at": started.isoformat(),
        "mode": args.mode,
        "trials": trials,
        "model": settings.llm_model if mode == "llm" else None,
        "graph_version": settings.graph_active_version,
        "decision_version": load_decision_config(config_dir() / "decision.yaml").version,
        "commit": _commit(),
        "selection": f"suite={args.suite} tags={sorted(args.tag)} cases={sorted(args.case)}",
    }
    expected = {c.id: c.expect.model_dump() for c in cases}
    report = build_report(run, results, expected)
    _, md = write_report(report, args.out or evals_dir() / "reports")
    print(md.read_text())
    print(f"report: {md}")
    failed_bar = any(b["ok"] is False for b in report["bars"])
    if (args.check_bars and failed_bar) or (args.strict and report["failures"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
