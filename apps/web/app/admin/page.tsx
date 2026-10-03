"use client";

import { useEffect, useState } from "react";
import { ApiError, api, post, signedIn } from "../../lib/api";
import { inr, label, when } from "../../lib/format";

type User = { id: string; email: string; role: string; authority_limit_minor: number; status: string };
type Config = {
  policies: { version: string; layer: string; effective_from: string; rules: { clause_id: string; text: string }[] }[];
  decision: { version: string; gate: Record<string, number>; weights: Record<string, number>;
    risk: Record<string, number>; kill_switch: boolean };
  graph: { version: string; nodes: { id: string; kind: string; task: string; waits_for: string | null }[];
    edges: { from: string; to: string }[] };
};

const ROLES = ["agent", "approver", "admin", "qc_operator", "analyst"];

export default function Admin() {
  const [users, setUsers] = useState<User[]>([]);
  const [config, setConfig] = useState<Config | null>(null);
  const [form, setForm] = useState({ email: "", password: "", role: "agent" });
  const [error, setError] = useState("");
  const [tab, setTab] = useState("policies");
  const [products, setProducts] = useState<{ sku: string; title: string; price_minor: number }[]>([]);
  const [order, setOrder] = useState({ phone: "", sku: "", days_since_delivery: 3, payment_method: "upi", final_sale: false });
  const [created, setCreated] = useState("");

  async function createOrder(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setCreated("");
    try {
      const r = await post<{ order_id: string }>("staff", "/admin/test-orders", order);
      setCreated(`Created ${r.order_id}. The customer can now start a return from "My orders".`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the order.");
    }
  }

  async function load() {
    const [u, c, p] = await Promise.all([
      api<User[]>("staff", "/admin/users"),
      api<Config>("staff", "/admin/config"),
      api<{ sku: string; title: string; price_minor: number }[]>("staff", "/admin/products"),
    ]);
    setUsers(u);
    setConfig(c);
    setProducts(p);
  }

  useEffect(() => {
    if (!signedIn("staff")) {
      window.location.href = "/console/login?next=/admin";
      return;
    }
    load().catch((e) => setError(e instanceof ApiError && e.status === 403 ? "Admins only." : "Could not load."));
  }, []);

  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await post("staff", "/admin/users", form);
      setForm({ email: "", password: "", role: "agent" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the user.");
    }
  }

  return (
    <>
      <h1>Admin studio</h1>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="tabs" role="toolbar" aria-label="Sections">
        {["policies", "thresholds", "graph", "users", "test orders"].map((t) => (
          <button key={t} aria-pressed={tab === t} onClick={() => setTab(t)}>{label(t)}</button>
        ))}
      </div>

      {config && tab === "policies" && config.policies.map((doc) => (
        <section className="card" key={doc.version}>
          <h2>{doc.layer === "legal" ? "Legal layer (locked)" : "Merchant policy"} · {doc.version}</h2>
          <p className="small muted">In force from {when(doc.effective_from)}. Edited as versioned YAML in config/policies; a new version applies to orders placed after its effective date.</p>
          <table><tbody>{doc.rules.map((r) => (
            <tr key={r.clause_id}><td style={{ whiteSpace: "nowrap" }}>{r.clause_id}</td><td>{r.text}</td></tr>
          ))}</tbody></table>
        </section>
      ))}

      {config && tab === "thresholds" && (
        <section className="card">
          <h2>Decision settings · {config.decision.version}</h2>
          <p>Kill switch: <span className={`chip ${config.decision.kill_switch ? "bad" : "ok"}`}>{config.decision.kill_switch ? "ON — nothing is automatic" : "off"}</span></p>
          <div className="grid">
            <div><h3>Autonomy gate</h3><table><tbody>{Object.entries(config.decision.gate).map(([k, v]) => (
              <tr key={k}><td>{label(k)}</td><td>{k.endsWith("_minor") ? inr(v) : v}</td></tr>))}</tbody></table></div>
            <div><h3>Option scoring weights</h3><table><tbody>{Object.entries(config.decision.weights).map(([k, v]) => (
              <tr key={k}><td>{label(k)}</td><td>{v}</td></tr>))}</tbody></table></div>
            <div><h3>Risk signal weights</h3><table><tbody>{Object.entries(config.decision.risk).map(([k, v]) => (
              <tr key={k}><td>{label(k)}</td><td>{k.endsWith("_minor") ? inr(v) : v}</td></tr>))}</tbody></table></div>
          </div>
          <p className="small muted">Values are bounded by hard limits in code; nothing (including self-improvement) can move the gate past them.</p>
        </section>
      )}

      {config && tab === "graph" && (
        <section className="card">
          <h2>Procedural graph · {config.graph.version} (read-only)</h2>
          <table>
            <thead><tr><th>Step</th><th>Kind</th><th>Waits for</th><th>Can go to</th></tr></thead>
            <tbody>{config.graph.nodes.map((n) => (
              <tr key={n.id}>
                <td><strong>{n.id}</strong><div className="small muted">{n.task}</div></td>
                <td>{n.kind}</td><td>{label(n.waits_for)}</td>
                <td className="small">{config.graph.edges.filter((e) => e.from === n.id).map((e) => e.to).join(", ") || "—"}</td>
              </tr>
            ))}</tbody>
          </table>
        </section>
      )}

      {tab === "test orders" && (
        <form className="card" onSubmit={createOrder}>
          <h2>Create a delivered test order</h2>
          <p className="small muted">For demos: gives a signed-up customer an order to return. Real orders come from the store&apos;s order system.</p>
          <div className="grid">
            <div><label htmlFor="o-phone">Customer mobile number</label>
              <input id="o-phone" inputMode="tel" required value={order.phone} onChange={(e) => setOrder({ ...order, phone: e.target.value })} /></div>
            <div><label htmlFor="o-sku">Product</label>
              <select id="o-sku" required value={order.sku} onChange={(e) => setOrder({ ...order, sku: e.target.value })}>
                <option value="">Choose…</option>
                {products.map((p) => <option key={p.sku} value={p.sku}>{p.title} · {p.sku} · {inr(p.price_minor)}</option>)}
              </select></div>
            <div><label htmlFor="o-days">Delivered how many days ago</label>
              <input id="o-days" type="number" min={0} max={365} value={order.days_since_delivery}
                onChange={(e) => setOrder({ ...order, days_since_delivery: Number(e.target.value) })} /></div>
            <div><label htmlFor="o-pay">Payment</label>
              <select id="o-pay" value={order.payment_method} onChange={(e) => setOrder({ ...order, payment_method: e.target.value })}>
                <option value="upi">UPI</option><option value="card">Card</option><option value="wallet">Wallet</option><option value="cod">Cash on delivery</option>
              </select></div>
          </div>
          <label className="row" style={{ fontWeight: 400 }}>
            <input type="checkbox" style={{ width: "auto" }} checked={order.final_sale} onChange={(e) => setOrder({ ...order, final_sale: e.target.checked })} />
            Final-sale item
          </label>
          <button className="primary" type="submit" style={{ marginTop: 10 }}>Create order</button>
          {created && <p className="banner" role="status">{created}</p>}
        </form>
      )}

      {tab === "users" && (
        <>
          <section className="card">
            <h2>Staff</h2>
            <table>
              <thead><tr><th>Email</th><th>Role</th><th>Authority limit</th><th>Status</th></tr></thead>
              <tbody>{users.map((u) => (
                <tr key={u.id}><td>{u.email}</td><td>{label(u.role)}</td><td>{inr(u.authority_limit_minor)}</td><td>{u.status}</td></tr>
              ))}</tbody>
            </table>
          </section>
          <form className="card" onSubmit={addUser}>
            <h2>Add staff member</h2>
            <div className="grid">
              <div><label htmlFor="u-email">Email</label>
                <input id="u-email" type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></div>
              <div><label htmlFor="u-password">Temporary password (12+ characters)</label>
                <input id="u-password" type="password" minLength={12} required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></div>
              <div><label htmlFor="u-role">Role</label>
                <select id="u-role" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                  {ROLES.map((r) => <option key={r} value={r}>{label(r)}</option>)}
                </select></div>
            </div>
            <button className="primary" type="submit" style={{ marginTop: 10 }}>Add</button>
          </form>
        </>
      )}
    </>
  );
}
