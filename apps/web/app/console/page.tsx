"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, post, signOut, signedIn } from "../../lib/api";
import { label, when } from "../../lib/format";

type CaseRow = { case_id: string; status: string; current_node: string; route: string | null; priority: number;
  order_id: string | null; created_at: string | null };
type Item = { id: string; case_id: string; queue: string; status: string; priority: number; reason: string;
  assignee_id: string | null; due_at: string; escalated: boolean };

const QUEUES = ["approval", "escalation", "fraud_review", "dispute", "review"];

export default function Console() {
  const [tab, setTab] = useState<string>("approval");
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [status, setStatus] = useState("");
  const [route, setRoute] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (tab === "cases") {
      const q = new URLSearchParams();
      if (status) q.set("status", status);
      if (route) q.set("route", route);
      setCases(await api<CaseRow[]>("staff", `/console/cases?${q}`));
    } else {
      setItems(await api<Item[]>("staff", `/console/queues/${tab}`));
    }
  }, [tab, status, route]);

  useEffect(() => {
    if (!signedIn("staff")) {
      window.location.href = "/console/login";
      return;
    }
    load().catch((e) => setError(e instanceof ApiError ? e.message : "Could not load."));
    const timer = setInterval(() => load().catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, [load]);

  async function claim(id: string) {
    try {
      await post("staff", `/console/queue-items/${id}/claim`);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not claim.");
    }
  }

  return (
    <>
      <div className="row spread">
        <h1>Support console</h1>
        <button onClick={() => signOut("staff")}>Sign out</button>
      </div>
      <div className="tabs" role="toolbar" aria-label="Views">
        {QUEUES.map((q) => (
          <button key={q} aria-pressed={tab === q} onClick={() => setTab(q)}>{label(q)} queue</button>
        ))}
        <button aria-pressed={tab === "cases"} onClick={() => setTab("cases")}>All cases</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {tab === "cases" ? (
        <section className="card">
          <div className="row">
            <div><label htmlFor="f-status">Status</label>
              <select id="f-status" value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">Any</option><option value="waiting">In progress</option>
                <option value="escalated">Escalated</option><option value="closed">Closed</option>
              </select></div>
            <div><label htmlFor="f-route">Route</label>
              <select id="f-route" value={route} onChange={(e) => setRoute(e.target.value)}>
                <option value="">Any</option><option value="auto">Auto</option>
                <option value="approval">Approval</option><option value="escalate">Escalate</option>
              </select></div>
          </div>
          <table>
            <thead><tr><th>Order</th><th>Status</th><th>Step</th><th>Route</th><th>Priority</th><th>Created</th><th /></tr></thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.case_id}>
                  <td>{c.order_id}</td><td>{label(c.status)}</td><td>{label(c.current_node)}</td>
                  <td>{label(c.route)}</td><td>{c.priority}</td><td>{when(c.created_at)}</td>
                  <td><Link href={`/console/cases/${c.case_id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <section className="card">
          <h2>{label(tab)} queue</h2>
          {items.length === 0 ? <p className="muted">Nothing waiting.</p> : (
            <table>
              <thead><tr><th>Priority</th><th>Why</th><th>Due</th><th>Owner</th><th /></tr></thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.id} data-testid="queue-item">
                    <td>{i.priority}{i.escalated && <span className="chip bad">overdue</span>}</td>
                    <td className="small">{i.reason || "—"}</td>
                    <td>{when(i.due_at)}</td>
                    <td>{i.assignee_id ? "assigned" : <button onClick={() => claim(i.id)}>Claim</button>}</td>
                    <td><Link href={`/console/cases/${i.case_id}`}>Open case</Link></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}
    </>
  );
}
