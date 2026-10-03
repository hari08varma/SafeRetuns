"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api, post, signOut } from "../../lib/api";
import { REASONS, STATUS_LABELS, WISHES, inr, label, when } from "../../lib/format";
import { ProductArt, daysLeft, variantLabel } from "../../lib/products";
import { type BotItem, SupportBot } from "./support-bot";

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
  const [name, setName] = useState("");
  const [picked, setPicked] = useState<{ order: Order; item: Item } | null>(null);

  useEffect(() => {
    api<Order[]>("customer", "/me/orders").then(setOrders).catch(() => setOrders([]));
    api<MyCase[]>("customer", "/cases").then(setCases).catch(() => setCases([]));
    api<{ name: string }>("customer", "/me/profile").then((p) => setName(p.name)).catch(() => {});
  }, []);

  const botItems: BotItem[] = (orders ?? []).filter((o) => o.delivered_at).flatMap((o) => o.items
    .filter((i) => !i.final_sale).map((i) => ({ orderId: o.order_id, itemId: i.item_id, sku: i.sku, title: i.title })));
  const active = cases.filter((c) => c.status !== "closed");
  const openFor = (o: Order, i: Item) => active.find((c) => c.order_id === o.order_id && c.sku === i.sku);
  const itemCount = (orders ?? []).reduce((n, o) => n + o.items.reduce((k, i) => k + i.qty, 0), 0);
  const spent = (orders ?? []).reduce((n, o) => n + o.total_minor, 0);

  return (
    <>
      <div className="row spread">
        <div>
          <h1>My orders</h1>
          {name && <p className="muted" style={{ margin: 0 }}>Welcome back, {name.split(" ")[0]}.</p>}
        </div>
        <button onClick={() => signOut("customer")}>Sign out</button>
      </div>
      <p className="banner">You are chatting with an AI assistant. You can ask for a person at any time.</p>
      {orders && orders.length > 0 && (
        <div className="tiles">
          <div className="tile"><div className="value">{orders.length}</div><div className="name">Orders</div></div>
          <div className="tile"><div className="value">{itemCount}</div><div className="name">Items bought</div></div>
          <div className="tile"><div className="value">{inr(spent)}</div><div className="name">Total spent</div></div>
          <div className="tile"><div className="value">{active.length}</div><div className="name">Returns in progress</div></div>
        </div>
      )}
      {cases.length > 0 && (
        <section className="card">
          <h2>My returns</h2>
          <table>
            <thead><tr><th>Order</th><th>Item</th><th>Status</th><th>Started</th><th /></tr></thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.case_id}>
                  <td>{c.order_id}</td><td>{c.sku}</td>
                  <td>
                    <span className={`chip ${c.status === "escalated" ? "warn" : c.status === "closed" ? "" : "ok"}`}>
                      {STATUS_LABELS[c.status] ?? c.status}
                    </span>
                    <span className="muted small">{label(c.current_node).toLowerCase()}</span>
                  </td>
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
      {orders?.length === 0 && (
        <section className="card empty">
          <span className="empty-icon" aria-hidden="true">
            <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
              strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 8 12 3 3 8v8l9 5 9-5V8Z" /><path d="m3 8 9 5 9-5M12 13v8" />
            </svg>
          </span>
          <h2>No orders yet</h2>
          <p className="muted">
            Orders placed with your mobile number appear here. Once an order is delivered, you can
            start a return from this page.
          </p>
          <Link href="/profile">Check your details →</Link>
        </section>
      )}
      {orders?.map((o) => (
        <section className="card order-card" key={o.order_id}>
          <div className="order-head">
            <div>
              <h2>Order {o.order_id}</h2>
              <span className="muted small">Placed {when(o.placed_at)} · {o.payment_method.toUpperCase()}</span>
            </div>
            <div className="order-meta">
              <span className={`chip ${o.delivered_at ? "ok" : ""}`}>
                {o.delivered_at
                  ? `Delivered ${new Date(o.delivered_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`
                  : label(o.status)}
              </span>
              <strong>{inr(o.total_minor)}</strong>
            </div>
          </div>
          <ul className="order-items">
            {o.items.map((i) => {
              const left = daysLeft(o.delivered_at);
              const inReturn = openFor(o, i);
              return (
                <li key={i.item_id}>
                  <ProductArt sku={i.sku} />
                  <div className="item-info">
                    <strong>{i.title}</strong>
                    <span className="muted small">
                      {[variantLabel(i.sku), `Qty ${i.qty}`, inr(i.unit_price_minor)].filter(Boolean).join(" · ")}
                    </span>
                    {i.final_sale ? <span className="chip warn">final sale</span> : left !== null && (
                      <span className="window">
                        <span className="window-bar"><span style={{ width: `${(left / 30) * 100}%` }} /></span>
                        <span className="small muted">{left > 0 ? `${left} days left to return` : "Return window closed"}</span>
                      </span>
                    )}
                  </div>
                  <div className="item-action">
                    {inReturn ? (
                      <Link href={`/cases/${inReturn.case_id}`} className="chip ok">Return in progress →</Link>
                    ) : (
                      <button onClick={() => setPicked({ order: o, item: i })} aria-label={`Return ${i.title} from ${o.order_id}`}>
                        Start a return
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      {orders && <SupportBot name={name} items={botItems} />}
    </>
  );
}
