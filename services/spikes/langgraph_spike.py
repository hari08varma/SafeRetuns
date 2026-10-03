"""Phase 0 LangGraph spike — proves the four properties the plan depends on:

  (a) a JSON graph compiles into a LangGraph StateGraph with conditional edges
  (b) a case paused at human approval survives a real process restart (Postgres checkpointer)
  (c) pause/resume works for both approve and reject
  (d) two graph versions run side by side; a case finishes on the version it started on

Plus: the refund node is idempotent (re-running it never creates a second refund).

Usage:  DATABASE_URL=postgresql://... uv run python spikes/langgraph_spike.py
Writes: docs/spike-reports/langgraph.md
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt

from returns_agent.graph.compiler import CaseState, Handler, compile_graph
from returns_agent.graph.schema import NodeSpec, load_graph

ROOT = Path(__file__).resolve().parents[2]
GRAPHS = {
    v: ROOT / "config" / "graphs" / f"{v.replace('-', '_')}.json" for v in ("spike-v1", "spike-v2")
}
REPORT = ROOT / "docs" / "spike-reports" / "langgraph.md"
DB_URL = os.environ.get("DATABASE_URL", "postgresql://returns:returns@localhost:5432/returns")


def setup_tables(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS spike_case (
               case_id text PRIMARY KEY, graph_version text NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS spike_refund_outbox (
               idempotency_key text PRIMARY KEY,
               case_id text NOT NULL,
               amount_minor int NOT NULL)"""
    )
    conn.commit()


def build_handlers(conn: psycopg.Connection[Any]) -> dict[str, Handler]:
    def noop(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {}

    def check_eligibility(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"eligible": state["facts"]["days_since_delivery"] <= 30}}

    def human_approval(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        decision = interrupt(
            {
                "case_id": state["case_id"],
                "ask": "Approve refund?",
                "amount_minor": state["facts"]["amount_minor"],
            }
        )
        return {"facts": {"approved": bool(decision["approve"])}}

    def issue_refund(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        # Side effect goes to an outbox row keyed by an idempotency key, so a re-run
        # after a crash can never create a second refund.
        key = f"{state['case_id']}:refund:item-1"
        conn.execute(
            "INSERT INTO spike_refund_outbox VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (key, state["case_id"], state["facts"]["amount_minor"]),
        )
        conn.commit()
        return {"facts": {"refund_key": key}}

    def notify_customer(state: CaseState, node: NodeSpec) -> dict[str, Any]:
        return {"facts": {"notified": True}}

    return {
        "llm": noop,
        "terminal": noop,
        "check_eligibility": check_eligibility,
        "human": human_approval,
        "issue_refund": issue_refund,
        "notify_customer": notify_customer,
    }


def start_case(
    saver: PostgresSaver, conn: psycopg.Connection[Any], case_id: str, version: str, days: int = 5
) -> None:
    conn.execute("INSERT INTO spike_case VALUES (%s, %s)", (case_id, version))
    conn.commit()
    graph = compile_graph(load_graph(GRAPHS[version]), build_handlers(conn), saver)
    graph.invoke(
        {
            "case_id": case_id,
            "graph_version": version,
            "facts": {"days_since_delivery": days, "amount_minor": 129900},
        },
        {"configurable": {"thread_id": case_id}},
    )


def resume_case(
    saver: PostgresSaver, conn: psycopg.Connection[Any], case_id: str, approve: bool
) -> dict[str, Any]:
    # The pinned version comes from our own table, not from the active version.
    row = conn.execute(
        "SELECT graph_version FROM spike_case WHERE case_id=%s", (case_id,)
    ).fetchone()
    assert row is not None
    graph = compile_graph(load_graph(GRAPHS[row[0]]), build_handlers(conn), saver)
    result: dict[str, Any] = graph.invoke(
        Command(resume={"approve": approve}), {"configurable": {"thread_id": case_id}}
    )
    return result


def waiting_at(
    saver: PostgresSaver, conn: psycopg.Connection[Any], case_id: str
) -> tuple[str, ...]:
    row = conn.execute(
        "SELECT graph_version FROM spike_case WHERE case_id=%s", (case_id,)
    ).fetchone()
    assert row is not None
    graph = compile_graph(load_graph(GRAPHS[row[0]]), build_handlers(conn), saver)
    return tuple(graph.get_state({"configurable": {"thread_id": case_id}}).next)


def refund_rows(conn: psycopg.Connection[Any], case_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) FROM spike_refund_outbox WHERE case_id=%s", (case_id,)
    ).fetchone()
    return int(row[0]) if row else 0


def run_all() -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    run = uuid.uuid4().hex[:8]

    def check(name: str, ok: bool, detail: str) -> None:
        results.append((name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    with PostgresSaver.from_conn_string(DB_URL) as saver, psycopg.connect(DB_URL) as conn:
        saver.setup()
        setup_tables(conn)

        # (a) compile both versions
        v1, v2 = load_graph(GRAPHS["spike-v1"]), load_graph(GRAPHS["spike-v2"])
        compile_graph(v1, build_handlers(conn), saver)
        compile_graph(v2, build_handlers(conn), saver)
        check(
            "a. JSON graph compiles to LangGraph",
            True,
            f"{v1.version}: {len(v1.nodes)} nodes, {v2.version}: {len(v2.nodes)} nodes",
        )

        # (b) real process restart: start in a child process, resume here
        case_b = f"case-b-{run}"
        child = subprocess.run(
            [sys.executable, __file__, "--start", case_b, "--version", "spike-v1"],
            capture_output=True,
            text=True,
        )
        paused = waiting_at(saver, conn, case_b)
        final = resume_case(saver, conn, case_b, approve=True)
        ok = (
            child.returncode == 0
            and paused == ("human_approval",)
            and final["current_node"] == "close_resolved"
            and refund_rows(conn, case_b) == 1
        )
        check(
            "b. Survives process restart",
            ok,
            f"child exit={child.returncode}, paused at {paused}, "
            f"finished at {final['current_node']}, refunds={refund_rows(conn, case_b)}",
        )

        # Idempotency: re-run the refund node directly — still exactly one refund row
        build_handlers(conn)["issue_refund"](
            {"case_id": case_b, "facts": {"amount_minor": 129900}},
            v1.node("issue_refund"),
        )
        check(
            "   Refund node idempotent on re-run",
            refund_rows(conn, case_b) == 1,
            f"refund rows after re-run = {refund_rows(conn, case_b)}",
        )

        # (c) pause/resume with reject
        case_c = f"case-c-{run}"
        start_case(saver, conn, case_c, "spike-v1")
        final_c = resume_case(saver, conn, case_c, approve=False)
        ok = final_c["current_node"] == "close_rejected" and refund_rows(conn, case_c) == 0
        check(
            "c. Pause/resume (approve and reject)",
            ok and results[1][1],
            f"reject path ended at {final_c['current_node']}, refunds={refund_rows(conn, case_c)}",
        )

        # (d) two versions side by side
        case_old, case_new = f"case-d1-{run}", f"case-d2-{run}"
        start_case(saver, conn, case_old, "spike-v1")  # started before v2 was activated
        start_case(saver, conn, case_new, "spike-v2")  # started after v2 was activated
        p_old = resume_case(saver, conn, case_old, approve=True)["path"]
        p_new = resume_case(saver, conn, case_new, approve=True)["path"]
        ok = "notify_customer" not in p_old and "notify_customer" in p_new
        check(
            "d. Versions side by side, pinned per case",
            ok,
            f"v1 path={'>'.join(p_old)}; v2 path={'>'.join(p_new)}",
        )
    return results


def write_report(results: list[tuple[str, bool, str]]) -> None:
    lines = [
        "# LangGraph spike report",
        "",
        "Generated by `services/spikes/langgraph_spike.py` (LangGraph 1.2.12, "
        "langgraph-checkpoint-postgres 3.1.2, real Postgres).",
        "",
        "| Check | Result | Detail |",
        "|---|---|---|",
        *(f"| {n.strip()} | {'PASS' if ok else 'FAIL'} | {d} |" for n, ok, d in results),
        "",
        f"**Verdict:** {'GO' if all(ok for _, ok, _ in results) else 'NO-GO'}",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start")
    parser.add_argument("--version", default="spike-v1")
    args = parser.parse_args()
    if args.start:  # child-process mode used by check (b)
        with PostgresSaver.from_conn_string(DB_URL) as saver, psycopg.connect(DB_URL) as conn:
            start_case(saver, conn, args.start, args.version)
        print(json.dumps({"started": args.start}))
        return
    results = run_all()
    write_report(results)
    sys.exit(0 if all(ok for _, ok, _ in results) else 1)


if __name__ == "__main__":
    main()
