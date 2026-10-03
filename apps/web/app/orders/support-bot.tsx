"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, post } from "../../lib/api";
import { OPTION_LABELS, inr } from "../../lib/format";
import { variantLabel } from "../../lib/products";

export type BotItem = { orderId: string; itemId: string; sku: string; title: string };
type Msg = { from: "bot" | "me" | "system"; text: string; at: number };
type CaseView = {
  case_id: string; status: string; current_node: string; waiting_for: string | null;
  reply: string; options: string[]; refund_total_minor: number | null;
};
type Step = "start" | "item" | "wish" | "reason" | "details" | "case";
type Choice = [string, string];

const STORE = "vapsi.support.v2";
const HELP: Choice[] = [
  ["refund", "Return & refund an item"],
  ["exchange", "Exchange an item"],
  ["damaged", "My item arrived damaged"],
  ["human", "Talk to a person"],
];
const WISH: Choice[] = [
  ["refund", "Refund"], ["exchange", "Exchange"], ["replacement", "Replacement"], ["store_credit", "Store credit"],
];
const REASON: Choice[] = [
  ["size_fit", "Size or fit issue"], ["damaged", "Arrived damaged"], ["defective", "Not working / defective"],
  ["wrong_item", "Wrong item received"], ["not_as_described", "Not as described"], ["changed_mind", "Changed my mind"],
];
const SENT_TO_TEAM = "✅ Your request has been sent to our support team. We'll respond very soon.";
const time = (at: number) => new Date(at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit" });
const withTeam = (v: CaseView) => v.status === "escalated" || v.current_node === "HUMAN_APPROVAL";

/** Support chat: guided options (like most store chats), every request handled by the
 *  returns agent on DeepSeek — understanding, policy, photo check, replies and routing. */
export function SupportBot({ name, items }: { name: string; items: BotItem[] }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [step, setStep] = useState<Step>("start");
  const [help, setHelp] = useState("");
  const [item, setItem] = useState<BotItem | null>(null);
  const [wish, setWish] = useState("");
  const [reason, setReason] = useState("");
  const [view, setView] = useState<CaseView | null>(null);
  const [seen, setSeen] = useState(0); // agent messages already shown
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [unread, setUnread] = useState(0);
  const end = useRef<HTMLDivElement>(null);
  const file = useRef<HTMLInputElement>(null);
  const first = name ? name.split(" ")[0] : "";

  useEffect(() => {
    try {
      const s = JSON.parse(sessionStorage.getItem(STORE) || "null");
      if (s) { setMsgs(s.msgs); setStep(s.step); setView(s.view); setSeen(s.seen); }
    } catch { /* storage unavailable */ }
  }, []);
  useEffect(() => {
    try { sessionStorage.setItem(STORE, JSON.stringify({ msgs, step, view, seen })); } catch { /* ignore */ }
  }, [msgs, step, view, seen]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, typing, step, view]);

  const add = (m: Omit<Msg, "at">) => setMsgs((all) => [...all, { ...m, at: Date.now() }]);
  const bot = (t: string, next?: Step, delay = 500) => {
    setTyping(true);
    setTimeout(() => { setTyping(false); add({ from: "bot", text: t }); if (next) setStep(next); }, delay);
  };

  function greet() {
    setMsgs([]); setStep("start"); setView(null); setSeen(0); setHelp(""); setItem(null); setWish(""); setReason("");
    bot(`Hi${first ? ` ${first}` : ""}! 👋 I'm the Vapsi assistant. What do you need help with today?`, "start", 300);
  }

  function openPanel() {
    setOpen(true);
    setUnread(0);
    if (!msgs.length) greet();
  }

  // ---- guided steps --------------------------------------------------------------------
  function pickHelp([value, label]: Choice) {
    add({ from: "me", text: label });
    setHelp(value);
    if (value === "exchange" || value === "refund") setWish(value);
    if (value === "damaged") setReason("damaged");
    bot(items.length ? "Sure. Which item is it about?" : "You have no delivered orders to return yet.", "item");
  }

  function pickItem(i: BotItem) {
    setItem(i);
    add({ from: "me", text: `${i.title}${variantLabel(i.sku) ? ` · ${variantLabel(i.sku)}` : ""}` });
    if (help === "human") return openCase(i, { human: true });
    if (help === "damaged") return bot("Sorry about that! What would you like us to do?", "wish");
    bot("Got it. What's the reason?", "reason");
  }

  function pickWish([value, label]: Choice) {
    setWish(value);
    add({ from: "me", text: label });
    if (reason) return bot("Anything you'd like to add? Describe the problem, or tap Skip.", "details");
    bot("What's the reason for the return?", "reason");
  }

  function pickReason([value, label]: Choice) {
    setReason(value);
    add({ from: "me", text: label });
    bot("Thanks. Describe the problem in a few words, or tap Skip.", "details");
  }

  function details(t: string | null) {
    if (t) add({ from: "me", text: t });
    else add({ from: "me", text: "Skip" });
    setText("");
    if (item) openCase(item, { note: t ?? "" });
  }

  // ---- the agent (backend, DeepSeek) ---------------------------------------------------
  const sync = useCallback(async (id: string, shown: number) => {
    const [v, all] = await Promise.all([
      api<CaseView>("customer", `/cases/${id}`),
      api<{ role: string; text: string }[]>("customer", `/cases/${id}/messages`),
    ]);
    setView(v);
    const agent = all.filter((m) => m.role !== "customer");
    if (agent.length > shown) {
      const fresh = agent.slice(shown);
      setMsgs((m) => [...m, ...fresh.map((f) => ({ from: "bot" as const, text: f.text, at: Date.now() }))]);
      setSeen(agent.length);
      if (!open) setUnread((u) => u + fresh.length);
    }
  }, [open]);

  useEffect(() => {
    if (!view || view.status === "closed") return;
    const id = view.case_id;
    const t = setInterval(() => sync(id, seen).catch(() => {}), 7000);
    return () => clearInterval(t);
  }, [view, seen, sync]);

  async function handled(v: CaseView, before: CaseView | null) {
    setView(v);
    add({ from: "bot", text: v.reply });
    if (withTeam(v) && !(before && withTeam(before))) add({ from: "bot", text: SENT_TO_TEAM });
    const all = await api<{ role: string; text: string }[]>("customer", `/cases/${v.case_id}/messages`).catch(() => []);
    setSeen(all.filter((m) => m.role !== "customer").length);
  }

  async function run(work: () => Promise<CaseView>) {
    setTyping(true);
    const before = view;
    try {
      await handled(await work(), before);
    } catch (err) {
      add({ from: "bot", text: err instanceof ApiError && err.status !== 500
        ? `Sorry, I couldn't do that: ${err.message}` : "Sorry, something went wrong on my side. Please try again." });
    } finally {
      setTyping(false);
    }
  }

  function openCase(i: BotItem, o: { human?: boolean; note?: string }) {
    setStep("case");
    const reasonText = REASON.find(([v]) => v === reason)?.[1] ?? "";
    const wishText = WISH.find(([v]) => v === wish)?.[1] ?? "";
    const message = o.human
      ? `I would like to talk to a person about my ${i.title}.`
      : [o.note, reasonText && `Reason: ${reasonText}.`, wishText && `I would like: ${wishText.toLowerCase()}.`]
          .filter(Boolean).join(" ");
    run(async () => {
      const v = await post<CaseView>("customer", "/cases", {
        order_id: i.orderId, item_id: i.itemId, qty: 1, message,
        reason_category: o.human ? null : reason || null,
        desired_resolution: o.human ? null : wish || null,
        wants_human: o.human || null,
      });
      add({ from: "system", text: `Return opened · ${v.case_id.slice(0, 8).toUpperCase()}` });
      return v;
    });
  }

  function send(raw: string) {
    const t = raw.trim();
    if (!t || typing) return;
    if (step === "details") return details(t);
    setText("");
    add({ from: "me", text: t });
    if (view?.waiting_for === "customer_message") {
      return run(() => post<CaseView>("customer", `/cases/${view.case_id}/messages`, { text: t }));
    }
    if (view?.waiting_for === "customer_upload") return bot("Please upload a photo of the item using the button below.");
    if (view) return bot(view.status === "closed" ? "This return is closed." : SENT_TO_TEAM);
    bot("Please choose one of the options above so I can help you faster.");
  }

  function upload(files: FileList | null) {
    if (!files?.length || !view) return;
    add({ from: "me", text: `📷 ${files.length} photo${files.length > 1 ? "s" : ""} uploaded` });
    const form = new FormData();
    Array.from(files).slice(0, 5).forEach((f) => form.append("files", f));
    const id = view.case_id;
    run(() => api<CaseView>("customer", `/cases/${id}/evidence`, { method: "POST", body: form }))
      .finally(() => { if (file.current) file.current.value = ""; });
  }

  function confirm(option: string | null) {
    if (!view) return;
    add({ from: "me", text: option ? OPTION_LABELS[option] ?? option : "No thanks" });
    run(() => post<CaseView>("customer", `/cases/${view.case_id}/confirm`, option ? { accept: true, option } : { accept: false }));
  }

  const chips = (list: Choice[], onPick: (c: Choice) => void) => (
    <div className="quick">{list.map((c) => <button type="button" key={c[0]} onClick={() => onPick(c)}>{c[1]}</button>)}</div>
  );
  const idle = !typing;

  return (
    <>
      <button type="button" className="chat-launcher" onClick={() => (open ? setOpen(false) : openPanel())}
        aria-expanded={open} aria-controls="support-panel">
        {open ? (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
        ) : (
          <>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" /></svg>
            <span>Talk to us</span>
          </>
        )}
        {!open && unread > 0 && <span className="chat-badge">{unread}</span>}
      </button>

      {open && (
        <section id="support-panel" className="chat-panel" aria-label="Support chat">
          <header className="chat-head">
            <span className="chat-avatar lg" aria-hidden="true">V<i className="online" /></span>
            <div className="chat-title">
              <strong>Vapsi Support</strong>
              <span>AI assistant · replies instantly · a person can join any time</span>
            </div>
            <button type="button" className="chat-icon" onClick={greet} title="New conversation" aria-label="New conversation">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
            </button>
          </header>

          {view && (
            <div className="chat-case">
              <span className={`chip ${withTeam(view) ? "warn" : view.status === "closed" ? "" : "ok"}`}>
                {withTeam(view) ? "With support team" : view.status === "closed" ? "Closed" : "In progress"}
              </span>
              <span className="small muted">{view.current_node.replace(/_/g, " ").toLowerCase()}</span>
              <Link href={`/cases/${view.case_id}`} className="small">Open return →</Link>
            </div>
          )}

          <div className="chat-body" aria-live="polite">
            {msgs.map((m, i) => m.from === "system" ? (
              <div key={i} className="chat-divider"><span>{m.text}</span></div>
            ) : (
              <div key={i} className={`chat-row ${m.from}`}>
                {m.from === "bot" && <span className="chat-avatar" aria-hidden="true">V</span>}
                <div className="chat-msg">
                  <div className={`bubble ${m.from === "me" ? "customer" : "agent"}`}>{m.text}</div>
                  <span className="chat-time">{time(m.at)}</span>
                </div>
              </div>
            ))}
            {typing && (
              <div className="chat-row bot">
                <span className="chat-avatar" aria-hidden="true">V</span>
                <div className="bubble agent typing" aria-label="Assistant is typing"><i /><i /><i /></div>
              </div>
            )}

            {idle && step === "start" && msgs.length > 0 && chips(HELP, pickHelp)}
            {idle && step === "item" && (
              <div className="quick">
                {items.map((i) => (
                  <button type="button" key={i.itemId} onClick={() => pickItem(i)}>
                    {i.title}{variantLabel(i.sku) ? ` · ${variantLabel(i.sku)}` : ""}
                  </button>
                ))}
              </div>
            )}
            {idle && step === "wish" && chips(WISH, pickWish)}
            {idle && step === "reason" && chips(REASON, pickReason)}
            {idle && step === "details" && chips([["skip", "Skip"]], () => details(null))}
            {idle && view?.waiting_for === "customer_upload" && (
              <div className="quick">
                <button type="button" className="upload" onClick={() => file.current?.click()}>📷 Upload photo</button>
              </div>
            )}
            {idle && view?.waiting_for === "customer_confirm" && (
              <div className="chat-offer">
                {view.refund_total_minor !== null && (
                  <span className="small muted">Refund amount: <strong>{inr(view.refund_total_minor)}</strong></span>
                )}
                <div className="quick">
                  {view.options.map((o) => <button type="button" key={o} onClick={() => confirm(o)}>{OPTION_LABELS[o] ?? o}</button>)}
                  <button type="button" onClick={() => confirm(null)}>No thanks</button>
                </div>
              </div>
            )}
            {idle && view?.status === "closed" && chips([["new", "Start a new request"]], greet)}
            <div ref={end} />
          </div>

          <form className="chat-composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden
              onChange={(e) => upload(e.target.files)} />
            <button type="button" className="chat-icon" disabled={view?.waiting_for !== "customer_upload" || typing}
              onClick={() => file.current?.click()} aria-label="Attach photos" title="Attach photos">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
            </button>
            <textarea rows={1} placeholder="Type your message…" aria-label="Message" value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }} />
            <button type="submit" className="chat-send" disabled={!text.trim() || typing} aria-label="Send">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></svg>
            </button>
          </form>
          <p className="chat-foot">Powered by Vapsi AI · decisions follow the store&apos;s return policy</p>
        </section>
      )}
    </>
  );
}
