"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, post } from "../../../../lib/api";
import { OPTION_LABELS, inr, label } from "../../../../lib/format";

type Packet = {
  case: { id: string; status: string; current_node: string; route: string | null; priority: number };
  customer: { phone: string | null; email: string | null; tier: string | null; stats: Record<string, number | boolean> };
  summary: string;
  suggested: { option: string | null; alternatives: { option: string; utility: number; customer_fit: number }[];
    pruned: Record<string, string>; refund_quote: { total_minor: number } | null };
  risk: { score?: number; signals?: string[] };
  evidence: { assessment: Record<string, unknown> | null;
    files: { id: string; thumbnail: string | null; signals: string[]; assessment: Record<string, unknown> | null }[] };
  policy: { eligible: boolean | null; clauses: { clause_id: string; result: string; text: string | null }[] };
  decision: { route: string; route_reason: string; rationale: string; confidence: { overall: number } } | null;
  approval: { status: string; action: string; amount_minor: number; required_approvals: number;
    signoffs: { role: string; decision: string; reason_code: string }[] } | null;
  goodwill: { amount_minor: number; reason_code: string; status: string }[];
  timeline: { type: string; role?: string; text?: string; action?: string; actor?: string; template?: string }[];
};
type Codes = Record<string, string[]>;

function Thumb({ id }: { id: string }) {
  const [src, setSrc] = useState<string>("");
  useEffect(() => {
    let url = "";
    api<Blob>("staff", `/console/evidence/${id}/thumbnail`)
      .then((b) => { url = URL.createObjectURL(b); setSrc(url); })
      .catch(() => undefined);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [id]);
  return src ? <img src={src} alt="Evidence photo uploaded by the customer" /> : <span className="muted small">loading…</span>;
}

function resultChip(result: string) {
  const tone = result === "failed" ? "bad" : result === "overridden" || result === "applied" ? "warn" : "ok";
  return <span className={`chip ${tone}`}>{result}</span>;
}

export default function ConsoleCase() {
  const { id } = useParams<{ id: string }>();
  const [p, setP] = useState<Packet | null>(null);
  const [codes, setCodes] = useState<Codes>({});
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  // form state
  const [decision, setDecision] = useState("approve");
  const [reason, setReason] = useState("");
  const [option, setOption] = useState("");
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState("resolved_by_human");
  const [gwAmount, setGwAmount] = useState("");
  const [gwReason, setGwReason] = useState("");

  const load = useCallback(async () => setP(await api<Packet>("staff", `/console/cases/${id}/handoff`)), [id]);
  useEffect(() => {
    load().catch((e) => setError(e instanceof ApiError ? e.message : "Could not load the case."));
    api<Codes>("staff", "/console/reason-codes").then(setCodes).catch(() => undefined);
  }, [load]);

  async function act(path: string, body: unknown, message: string) {
    setError("");
    setDone("");
    try {
      await post("staff", path, body);
      setDone(message);
      setNote("");
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Action failed.");
    }
  }

  if (!p) return <p className="muted">{error || "Loading…"}</p>;
  const node = p.case.current_node;
  const open = p.case.status !== "closed";
  const resolveCodes = codes.resolve ?? [];

  return (
    <>
      <p><Link href="/console">← Console</Link></p>
      <div className="row spread">
        <h1>Case {p.case.id.slice(0, 8)}</h1>
        <span className="row">
          <span className="chip">{label(p.case.status)}</span>
          <span className="chip">{label(node)}</span>
          {p.case.route && <span className="chip">route: {p.case.route}</span>}
        </span>
      </div>

      <section className="card">
        <h2>AI case summary</h2>
        <p data-testid="summary">{p.summary}</p>
        {p.decision && <p className="small muted">Route reason: {p.decision.route_reason} · confidence {p.decision.confidence?.overall}</p>}
      </section>

      <div className="grid">
        <section className="card">
          <h2>Suggested action</h2>
          <p><strong>{OPTION_LABELS[p.suggested.option ?? ""] ?? p.suggested.option ?? "—"}</strong>
            {p.suggested.refund_quote && <> · refund {inr(p.suggested.refund_quote.total_minor)}</>}</p>
          <table>
            <thead><tr><th>Option</th><th>Score</th><th>Fit</th></tr></thead>
            <tbody>{p.suggested.alternatives.map((a) => (
              <tr key={a.option}><td>{OPTION_LABELS[a.option] ?? a.option}</td><td>{a.utility.toFixed(2)}</td><td>{a.customer_fit}</td></tr>
            ))}</tbody>
          </table>
          {Object.entries(p.suggested.pruned).map(([o, why]) => <p key={o} className="small muted">Not offered: {o} ({why})</p>)}
        </section>
        <section className="card">
          <h2>Risk</h2>
          <p>Score <strong>{p.risk.score ?? 0}</strong></p>
          <div>{(p.risk.signals ?? []).length ? p.risk.signals!.map((s) => <span key={s} className="chip warn">{label(s)}</span>) : <span className="muted">No signals</span>}</div>
          <h3 style={{ marginTop: 12 }}>Customer</h3>
          <p className="small">{p.customer.phone} · {p.customer.email} · {p.customer.tier}</p>
          <p className="small muted">{Object.entries(p.customer.stats).map(([k, v]) => `${label(k)}: ${v}`).join(" · ")}</p>
        </section>
      </div>

      <section className="card">
        <h2>Policy clauses</h2>
        <table>
          <tbody>{p.policy.clauses.map((c) => (
            <tr key={c.clause_id}><td style={{ whiteSpace: "nowrap" }}>{c.clause_id}</td><td>{resultChip(c.result)}</td><td className="small">{c.text}</td></tr>
          ))}</tbody>
        </table>
      </section>

      {p.evidence.files.length > 0 && (
        <section className="card">
          <h2>Evidence</h2>
          <div className="thumbs">
            {p.evidence.files.map((f) => (
              <figure key={f.id} style={{ margin: 0 }}>
                <Thumb id={f.id} />
                <figcaption className="small">{f.signals.length ? f.signals.map((s) => <span key={s} className="chip bad">{label(s)}</span>) : <span className="chip ok">no flags</span>}</figcaption>
              </figure>
            ))}
          </div>
          {p.evidence.assessment && <pre>{JSON.stringify(p.evidence.assessment, null, 2)}</pre>}
        </section>
      )}

      {open && node === "HUMAN_APPROVAL" && p.approval && (
        <section className="card" aria-label="Approval">
          <h2>Approval</h2>
          <p>{OPTION_LABELS[p.approval.action] ?? p.approval.action} · {inr(p.approval.amount_minor)} · {p.approval.status}
            {p.approval.required_approvals > 1 && <> · <strong>two approvers required</strong> ({p.approval.signoffs.length}/2)</>}</p>
          <div className="grid">
            <div><label htmlFor="decision">Decision</label>
              <select id="decision" value={decision} onChange={(e) => { setDecision(e.target.value); setReason(""); }}>
                <option value="approve">Approve</option><option value="modify">Approve a different option</option><option value="reject">Reject</option>
              </select></div>
            <div><label htmlFor="reason">Reason code</label>
              <select id="reason" value={reason} onChange={(e) => setReason(e.target.value)} required>
                <option value="">Choose…</option>
                {(codes[decision] ?? []).map((c) => <option key={c} value={c}>{label(c)}</option>)}
              </select></div>
            {decision === "modify" && (
              <div><label htmlFor="option">Option</label>
                <select id="option" value={option} onChange={(e) => setOption(e.target.value)}>
                  <option value="">Choose…</option>
                  {p.suggested.alternatives.map((a) => <option key={a.option} value={a.option}>{OPTION_LABELS[a.option] ?? a.option}</option>)}
                </select></div>
            )}
          </div>
          <label htmlFor="note">Note</label>
          <textarea id="note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="primary" disabled={!reason} style={{ marginTop: 10 }}
            onClick={() => act(`/console/cases/${id}/approval`, { decision, reason_code: reason, option: option || null, note }, "Decision recorded.")}>
            Submit decision
          </button>
        </section>
      )}

      {open && (node === "ESCALATE" || node === "DISPUTE") && (
        <section className="card" aria-label="Resolve">
          <h2>Resolve</h2>
          <div className="grid">
            <div><label htmlFor="outcome">Outcome</label>
              <select id="outcome" value={outcome} onChange={(e) => setOutcome(e.target.value)}>
                <option value="resolved_by_human">Resolved</option><option value="rejected">Rejected</option><option value="cancelled">Cancelled</option>
              </select></div>
            <div><label htmlFor="r-reason">Reason code</label>
              <select id="r-reason" value={reason} onChange={(e) => setReason(e.target.value)}>
                <option value="">Choose…</option>{resolveCodes.map((c) => <option key={c} value={c}>{label(c)}</option>)}
              </select></div>
          </div>
          <label htmlFor="r-note">Note</label>
          <textarea id="r-note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button className="primary" disabled={!reason} style={{ marginTop: 10 }}
            onClick={() => act(`/console/cases/${id}/resolve`, { outcome, reason_code: reason, note }, "Case resolved.")}>
            Resolve case
          </button>
        </section>
      )}

      {open && node === "INSPECT_QC" && (
        <section className="card" aria-label="Quality check">
          <h2>Quality check</h2>
          <div className="row">
            <button className="primary" onClick={() => act(`/console/cases/${id}/qc`, { passed: true, grade: "A" }, "QC passed.")}>Passed</button>
            <button className="danger" onClick={() => act(`/console/cases/${id}/qc`, { passed: false, grade: "D" }, "QC failed.")}>Failed</button>
          </div>
        </section>
      )}

      <section className="card" aria-label="Goodwill">
        <h2>Goodwill</h2>
        <p className="small muted">Store credit for this case only, within your limit. It never changes policy.</p>
        <div className="grid">
          <div><label htmlFor="gw-amount">Amount (₹)</label>
            <input id="gw-amount" type="number" min={1} value={gwAmount} onChange={(e) => setGwAmount(e.target.value)} /></div>
          <div><label htmlFor="gw-reason">Reason code</label>
            <select id="gw-reason" value={gwReason} onChange={(e) => setGwReason(e.target.value)}>
              <option value="">Choose…</option>{(codes.goodwill ?? []).map((c) => <option key={c} value={c}>{label(c)}</option>)}
            </select></div>
        </div>
        <button disabled={!gwAmount || !gwReason} style={{ marginTop: 10 }}
          onClick={() => act(`/console/cases/${id}/goodwill`, { amount_minor: Math.round(Number(gwAmount) * 100), reason_code: gwReason }, "Goodwill granted.")}>
          Grant goodwill
        </button>
        {p.goodwill.map((g, i) => <p key={i} className="small">{inr(g.amount_minor)} · {label(g.reason_code)} · {g.status}</p>)}
      </section>

      {done && <p className="banner" role="status">{done}</p>}
      {error && <p className="error" role="alert">{error}</p>}

      <section className="card">
        <h2>Timeline</h2>
        <table>
          <tbody>{p.timeline.map((t, i) => (
            <tr key={i} className="small">
              <td>{t.type}</td>
              <td>{t.type === "message" ? `${t.role}: ${t.text}` : t.type === "audit" ? `${t.action} (${t.actor})` : t.template ?? ""}</td>
            </tr>
          ))}</tbody>
        </table>
      </section>
    </>
  );
}
