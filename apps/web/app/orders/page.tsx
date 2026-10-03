"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, post, signOut } from "../../lib/api";
import { REASONS, STATUS_LABELS, WISHES, inr, label, when } from "../../lib/format";

type Item = { item_id: string; sku: string; title: string; qty: number; unit_price_minor: number; final_sale: boolean };
type Order = { order_id: string; status: string; total_minor: number; payment_method: string;
  placed_at: string; delivered_at: string | null; items: Item[] };
type MyCase = { case_id: string; status: string; current_node: string; order_id: string; sku: string | null; created_at: string };

function StartReturn({ order, item, onCancel }: { order: Order; item: Item; onCancel: () => void }) {
  const router = useRouter();
  const [message, setMessage] = useState("");
  const [reason, setReason] = useState("");
  const [wish, setWish] = useState("");
  const [variant, setVariant] = useState("");
  const [gift, setGift] = useState(false);
  const [qty, setQty] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const family = item.sku.split("-").slice(0, -1).join("-");
    try {
      const view = await post<{ case_id: string }>("customer", "/cases", {
        order_id: order.order_id,
        item_id: item.item_id,
        qty,
        message,
        reason_category: reason || null,
        desired_resolution: wish || null,
        exchange_sku: wish === "exchange" && variant ? `${family}-${variant.trim()}` : null,
        is_gift: gift || null,
      });
      router.push(`/cases/${view.case_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the return.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card" aria-label={`Return ${item.title}`}>
      <h3>Return: {item.title}</h3>
      <label htmlFor="message">What happened?</label>
      <textarea id="message" required value={message} onChange={(e) => setMessage(e.target.value)}
        placeholder="Tell us in your own words — any language is fine." />
      <div className="grid">
        <div>
          <label htmlFor="reason">Reason (optional)</label>
          <select id="reason" value={reason} onChange={(e) => setReason(e.target.value)}>
            {REASONS.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="wish">What would you like? (optional)</label>
          <select id="wish" value={wish} onChange={(e) => setWish(e.target.value)}>
            {WISHES.map(([v, t]) => <option key={v} value={v}>{t}</option>)}
          </select>
        </div>
        {wish === "exchange" && (
          <div>
            <label htmlFor="variant">Size or colour you want</label>
            <input id="variant" placeholder="e.g. L or white" value={variant} onChange={(e) => setVariant(e.target.value)} />
          </div>
        )}
        {item.qty > 1 && (
          <div>
            <label htmlFor="qty">How many?</label>
            <input id="qty" type="number" min={1} max={item.qty} value={qty} onChange={(e) => setQty(Number(e.target.value))} />
          </div>
        )}
      </div>
      <label className="row" style={{ fontWeight: 400 }}>
        <input type="checkbox" checked={gift} onChange={(e) => setGift(e.target.checked)} style={{ width: "auto" }} />
        I received this as a gift
      </label>
      {error && <p className="error" role="alert">{error}</p>}
      <div className="row">
        <button className="primary" type="submit" disabled={busy}>{busy ? "Starting…" : "Start return"}</button>
        <button type="button" onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

export default function Orders() {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [cases, setCases] = useState<MyCase[]>([]);
  const [picked, setPicked] = useState<{ order: Order; item: Item } | null>(null);

  useEffect(() => {
    api<Order[]>("customer", "/me/orders").then(setOrders).catch(() => setOrders([]));
    api<MyCase[]>("customer", "/cases").then(setCases).catch(() => setCases([]));
  }, []);

  return (
    <>
      <div className="row spread">
        <h1>My orders</h1>
        <button onClick={() => signOut("customer")}>Sign out</button>
      </div>
      <p className="banner">You are chatting with an AI assistant. You can ask for a person at any time.</p>
      {cases.length > 0 && (
        <section className="card">
          <h2>My returns</h2>
          <table>
            <thead><tr><th>Order</th><th>Item</th><th>Status</th><th>Started</th><th /></tr></thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.case_id}>
                  <td>{c.order_id}</td><td>{c.sku}</td>
                  <td>{STATUS_LABELS[c.status] ?? c.status} · {label(c.current_node).toLowerCase()}</td>
                  <td>{when(c.created_at)}</td>
                  <td><Link href={`/cases/${c.case_id}`}>Open</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {picked && <StartReturn {...picked} onCancel={() => setPicked(null)} />}
      {orders === null && <p className="muted">Loading…</p>}
      {orders?.map((o) => (
        <section className="card" key={o.order_id}>
          <div className="row spread">
            <h2>{o.order_id}</h2>
            <span className="muted small">
              {label(o.status)} · {o.delivered_at ? `delivered ${when(o.delivered_at)}` : `placed ${when(o.placed_at)}`} · {o.payment_method.toUpperCase()}
            </span>
          </div>
          <table>
            <tbody>
              {o.items.map((i) => (
                <tr key={i.item_id}>
                  <td>{i.title} <span className="muted small">({i.sku})</span>{i.final_sale && <span className="chip warn">final sale</span>}</td>
                  <td>{i.qty} × {inr(i.unit_price_minor)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button onClick={() => setPicked({ order: o, item: i })} aria-label={`Return ${i.title} from ${o.order_id}`}>
                      Start a return
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ))}
    </>
  );
}
