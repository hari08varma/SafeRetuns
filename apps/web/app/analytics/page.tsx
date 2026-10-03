"use client";

import { useEffect, useState } from "react";
import { ApiError, api, signedIn } from "../../lib/api";
import { inr, label } from "../../lib/format";

type Summary = {
  days: number; orders: number; cases: number; open: number; escalated: number; closed: number;
  routes: Record<string, number>; auto_resolution_rate: number | null; avg_resolution_hours: number | null;
  resolutions: Record<string, number>;
  money: { refunded_minor: number; store_credit_minor: number; kept_by_exchange_minor: number;
    reverse_shipping_avoided: number; goodwill_minor: number };
  by_reason: Record<string, number>; by_category: Record<string, number>;
  top_products: { family: string; title: string; returns: number; units_sold: number; return_rate: number | null; top_reason: string }[];
  insights: { family: string; title: string; problem: string; share: number; returns: number; action: string }[];
  sla_breaches: number;
};

/** One series, one hue: horizontal bars with the value as text beside each bar. */
function Bars({ data, format = String }: { data: Record<string, number>; format?: (n: number) => string }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <p className="muted">No data yet.</p>;
  return (
    <div className="bars" role="list">
      {entries.map(([k, v]) => (
        <div className="bar-row" role="listitem" key={k} title={`${label(k)}: ${format(v)}`}>
          <span>{label(k)}</span>
          <span className="bar-track"><span className="bar" style={{ display: "block", width: `${(v / max) * 100}%` }} /></span>
          <span className="bar-value">{format(v)}</span>
        </div>
      ))}
    </div>
  );
}

function Tile({ name, value }: { name: string; value: string }) {
  return <div className="tile"><div className="value">{value}</div><div className="name">{name}</div></div>;
}

export default function Analytics() {
  const [days, setDays] = useState(30);
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!signedIn("staff")) {
      window.location.href = "/console/login?next=/analytics";
      return;
    }
    api<Summary>("staff", `/analytics/summary?days=${days}`)
      .then(setS)
      .catch((e) => setError(e instanceof ApiError && e.status === 403 ? "Analysts and admins only." : "Could not load."));
  }, [days]);

  return (
    <>
      <div className="row spread">
        <h1>Returns analytics</h1>
        <div><label htmlFor="days" className="small">Period</label>
          <select id="days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
          </select></div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      {s && (
        <>
          <div className="tiles">
            <Tile name="Returns started" value={String(s.cases)} />
            <Tile name="Resolved automatically" value={s.auto_resolution_rate === null ? "—" : `${Math.round(s.auto_resolution_rate * 100)}%`} />
            <Tile name="Average time to resolve" value={s.avg_resolution_hours === null ? "—" : `${s.avg_resolution_hours} h`} />
            <Tile name="With a person now" value={String(s.escalated)} />
            <Tile name="Refunded" value={inr(s.money.refunded_minor)} />
            <Tile name="Kept by exchange / replacement" value={inr(s.money.kept_by_exchange_minor)} />
            <Tile name="Pickups avoided (keep the item)" value={String(s.money.reverse_shipping_avoided)} />
            <Tile name="SLA breaches" value={String(s.sla_breaches)} />
          </div>

          <section className="card">
            <h2>Fix these products to prevent returns</h2>
            {s.insights.length === 0 ? (
              <p className="muted">No product has enough returns with one clear cause yet.</p>
            ) : (
              <table>
                <thead><tr><th>Product</th><th>Problem</th><th>Share of its returns</th><th>Suggested fix</th></tr></thead>
                <tbody>{s.insights.map((i) => (
                  <tr key={`${i.family}-${i.problem}`}>
                    <td>{i.title}<div className="small muted">{i.returns} returns</div></td>
                    <td>{i.problem}</td><td>{Math.round(i.share * 100)}%</td><td>{i.action}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </section>

          <div className="grid">
            <section className="card"><h2>Why customers return</h2><Bars data={s.by_reason} /></section>
            <section className="card"><h2>How returns were resolved</h2><Bars data={s.resolutions} /></section>
            <section className="card"><h2>Returns by category</h2><Bars data={s.by_category} /></section>
            <section className="card"><h2>Decision route</h2><Bars data={s.routes} /></section>
          </div>

          <section className="card">
            <h2>Most-returned products</h2>
            <table>
              <thead><tr><th>Product</th><th>Returns</th><th>Units sold</th><th>Return rate</th><th>Top reason</th></tr></thead>
              <tbody>{s.top_products.map((p) => (
                <tr key={p.family}><td>{p.title}</td><td>{p.returns}</td><td>{p.units_sold}</td>
                  <td>{p.return_rate === null ? "—" : `${Math.round(p.return_rate * 100)}%`}</td><td>{label(p.top_reason)}</td></tr>
              ))}</tbody>
            </table>
          </section>
        </>
      )}
    </>
  );
}
