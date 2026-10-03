"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, post } from "../../../lib/api";
import { METHOD_LABELS, OPTION_LABELS, STATUS_LABELS, inr } from "../../../lib/format";

type View = { case_id: string; status: string; current_node: string; waiting_for: string | null; reply: string;
  options: string[]; refund_total_minor: number | null; refund_methods: string[] };
type Msg = { role: string; text: string };
type Entry = { type: string; label?: string; at: string };

const MONEY = new Set(["refund", "store_credit", "keep_item_refund"]);
const POLL_MS = 3000; // simple polling keeps the page live without a socket server

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const [view, setView] = useState<View | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [milestones, setMilestones] = useState<Entry[]>([]);
  const [text, setText] = useState("");
  const [option, setOption] = useState("");
  const [method, setMethod] = useState("");
  const [files, setFiles] = useState<FileList | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const chatEnd = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const [v, m, t] = await Promise.all([
      api<View>("customer", `/cases/${id}`),
      api<Msg[]>("customer", `/cases/${id}/messages`),
      api<Entry[]>("customer", `/cases/${id}/timeline`),
    ]);
    setView(v);
    setMessages(m);
    setMilestones(t.filter((e) => e.type === "milestone"));
  }, [id]);

  useEffect(() => {
    load().catch(() => setError("Could not load this return."));
    const timer = setInterval(() => load().catch(() => undefined), POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => { chatEnd.current?.scrollIntoView({ block: "nearest" }); }, [messages.length]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await fn();
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    act(() => post("customer", `/cases/${id}/messages`, { text })).then(() => setText(""));
  };

  const upload = (e: React.FormEvent) => {
    e.preventDefault();
    if (!files?.length) return;
    const form = new FormData();
    Array.from(files).forEach((f) => form.append("files", f));
    act(() => api("customer", `/cases/${id}/evidence`, { method: "POST", body: form }));
  };

  const confirm = (accept: boolean) =>
    act(() =>
      post("customer", `/cases/${id}/confirm`, {
        accept,
        option: accept && option ? option : null,
        refund_method: accept && method ? method : null,
      }),
    );

  const askForPerson = () => {
    if (view?.status === "closed") {
      act(async () => {
        await post("customer", `/cases/${id}/review`, { reason: "I would like a person to review this decision." });
        setNotice("A member of our team will review this decision.");
      });
    } else if (view?.waiting_for === "customer_message") {
      act(() => post("customer", `/cases/${id}/messages`, { text: "I would like to talk to a person." }));
    } else {
      setNotice("Your case is already with our team or in progress. We will update you here.");
    }
  };

  if (!view) return <p className="muted">{error || "Loading…"}</p>;
  const chosen = option || view.options[0] || "";
  const showMethods = view.waiting_for === "customer_confirm" && MONEY.has(chosen) && chosen !== "store_credit"
    && view.refund_methods.length > 1;

  return (
    <>
      <p><Link href="/orders">← My orders</Link></p>
      <p className="banner">You are chatting with an AI assistant. Decisions follow the store's return policy; a person reviews high-value or unclear cases.</p>
      <section className="card">
        <div className="row spread">
          <h1>Your return</h1>
          <span className="chip" data-testid="status">{STATUS_LABELS[view.status] ?? view.status}</span>
        </div>
        <ul className="steps" aria-label="Progress">
          <li className="done">Request received</li>
          {milestones.map((m, i) => <li key={i} className="done">{m.label}</li>)}
        </ul>
      </section>

      <section className="card" aria-live="polite">
        <h2>Conversation</h2>
        <div className="chat" data-testid="chat">
          {messages.map((m, i) => (
            <div key={i} className={`bubble ${m.role === "customer" ? "customer" : "agent"}`}>{m.text}</div>
          ))}
          <div ref={chatEnd} />
        </div>
      </section>

      {view.waiting_for === "customer_confirm" && (
        <section className="card" data-testid="offer">
          <h2>Choose how to resolve this</h2>
          {view.refund_total_minor !== null && view.options.some((o) => MONEY.has(o)) && (
            <p>Refund amount: <strong>{inr(view.refund_total_minor)}</strong></p>
          )}
          <fieldset style={{ border: 0, padding: 0 }}>
            <legend className="muted small">Options</legend>
            {view.options.map((o) => (
              <label key={o} className="row" style={{ fontWeight: 400 }}>
                <input type="radio" name="option" value={o} checked={chosen === o}
                  onChange={() => setOption(o)} style={{ width: "auto" }} />
                {OPTION_LABELS[o] ?? o}
              </label>
            ))}
          </fieldset>
          {showMethods && (
            <>
              <label htmlFor="method">Refund to</label>
              <select id="method" value={method} onChange={(e) => setMethod(e.target.value)}>
                <option value="">Recommended</option>
                {view.refund_methods.map((m) => <option key={m} value={m}>{METHOD_LABELS[m] ?? m}</option>)}
              </select>
            </>
          )}
          <div className="row" style={{ marginTop: 12 }}>
            <button className="primary" disabled={busy} onClick={() => confirm(true)}>Confirm</button>
            <button disabled={busy} onClick={() => confirm(false)}>No thanks</button>
          </div>
        </section>
      )}

      {view.waiting_for === "customer_upload" && (
        <form className="card" onSubmit={upload}>
          <h2>Add photos</h2>
          <label htmlFor="photos">Photos of the item (JPEG, PNG or WebP, up to 5)</label>
          <input id="photos" type="file" accept="image/jpeg,image/png,image/webp" multiple
            onChange={(e) => setFiles(e.target.files)} />
          <button className="primary" type="submit" disabled={busy || !files?.length} style={{ marginTop: 10 }}>Upload</button>
        </form>
      )}

      {view.waiting_for === "customer_message" && (
        <form className="card" onSubmit={send}>
          <label htmlFor="reply">Your reply</label>
          <textarea id="reply" value={text} onChange={(e) => setText(e.target.value)} />
          <button className="primary" type="submit" disabled={busy || !text.trim()} style={{ marginTop: 10 }}>Send</button>
        </form>
      )}

      <div className="row">
        <button onClick={askForPerson} disabled={busy}>
          {view.status === "closed" ? "Ask for a human review" : "Talk to a person"}
        </button>
      </div>
      {notice && <p className="banner" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}
