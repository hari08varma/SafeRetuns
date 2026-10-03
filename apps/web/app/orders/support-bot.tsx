"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, post } from "../../lib/api";
import { REASONS, WISHES } from "../../lib/format";
import { variantLabel } from "../../lib/products";

export type BotItem = { orderId: string; itemId: string; sku: string; title: string };
type Msg = { from: "bot" | "me"; text: string };
type Step = "item" | "reason" | "details" | "wish" | "sending" | "done";

const HUMAN = "__human__";

/** Guided support chat: asks a few questions, then opens the return with the agent. */
export function SupportBot({ items, name }: { items: BotItem[]; name: string }) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [step, setStep] = useState<Step>("item");
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const [item, setItem] = useState<BotItem | null>(null);
  const [reason, setReason] = useState("");
  const [details, setDetails] = useState("");
  const [caseId, setCaseId] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs, typing, step]);

  function say(textToSay: string, next?: Step) {
    setTyping(true);
    setTimeout(() => {
      setTyping(false);
      setMsgs((m) => [...m, { from: "bot", text: textToSay }]);
      if (next) setStep(next);
    }, 650);
  }

  function start() {
    setOpen(true);
    if (msgs.length) return;
    const hi = name ? `Hi ${name.split(" ")[0]}! ` : "Hi! ";
    say(items.length
      ? `${hi}I'm Vapsi, your returns assistant. Which item do you need help with?`
      : `${hi}I'm Vapsi. You have no delivered orders yet, so there is nothing to return right now.`, "item");
  }

  function me(t: string) {
    setMsgs((m) => [...m, { from: "me", text: t }]);
  }

  function pickItem(i: BotItem) {
    setItem(i);
    me(`${i.title}${variantLabel(i.sku) ? ` (${variantLabel(i.sku)})` : ""}`);
    say(`Got it: your ${i.title}. What went wrong?`, "reason");
  }

  function pickReason(value: string, text: string) {
    setReason(value);
    me(text);
    say("Sorry about that. Tell me a little more in your own words (any language is fine).", "details");
  }

  function sendDetails(e?: React.FormEvent) {
    e?.preventDefault();
    const t = text.trim();
    if (!t) return;
    setDetails(t);
    setText("");
    me(t);
    say("Thanks. What would you like us to do?", "wish");
  }

  async function pickWish(value: string, label: string) {
    if (!item) return;
    me(label);
    setStep("sending");
    setTyping(true);
    const human = value === HUMAN;
    const reasonText = REASONS.find(([v]) => v === reason)?.[1] ?? "";
    const message = [details || reasonText, human ? "I would like to talk to a person." : ""]
      .filter(Boolean).join(" ");
    try {
      const view = await post<{ case_id: string; reply: string }>("customer", "/cases", {
        order_id: item.orderId,
        item_id: item.itemId,
        qty: 1,
        message,
        reason_category: reason || null,
        desired_resolution: human ? null : value || null,
        wants_human: human || null,
      });
      setTyping(false);
      setCaseId(view.case_id);
      setMsgs((m) => [...m, { from: "bot", text: view.reply ||
        (human ? "I've passed your case to a specialist. They will reply here." : "Your return is started.") }]);
      setStep("done");
    } catch (err) {
      setTyping(false);
      setMsgs((m) => [...m, { from: "bot",
        text: err instanceof ApiError ? `Sorry, I couldn't start that: ${err.message}` : "Sorry, something went wrong. Please try again." }]);
      setStep("wish");
    }
  }

  function restart() {
    setMsgs([]); setItem(null); setReason(""); setDetails(""); setCaseId(null); setStep("item");
    setOpen(false);
    setTimeout(start, 0);
  }

  return (
    <>
      <button type="button" className="support-fab" onClick={() => (open ? setOpen(false) : start())}
        aria-expanded={open} aria-controls="support-panel">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2Z" />
        </svg>
        {open ? "Close" : "Support"}
      </button>

      {open && (
        <section id="support-panel" className="support-panel" aria-label="Support chat">
          <header>
            <span className="logo" aria-hidden="true">V</span>
            <div>
              <strong>Vapsi assistant</strong>
              <span className="small">AI assistant · you can ask for a person any time</span>
            </div>
          </header>

          <div className="support-body" aria-live="polite">
            {msgs.map((m, i) => (
              <div key={i} className={`bubble ${m.from === "me" ? "customer" : "agent"}`}>{m.text}</div>
            ))}
            {typing && <div className="bubble agent typing" aria-label="Assistant is typing"><i /><i /><i /></div>}

            {!typing && step === "item" && items.length > 0 && (
              <div className="quick">
                {items.map((i) => (
                  <button type="button" key={i.itemId} onClick={() => pickItem(i)}>
                    {i.title}{variantLabel(i.sku) ? ` · ${variantLabel(i.sku)}` : ""}
                  </button>
                ))}
              </div>
            )}
            {!typing && step === "reason" && (
              <div className="quick">
                {REASONS.filter(([v]) => v).map(([v, t]) => (
                  <button type="button" key={v} onClick={() => pickReason(v, t)}>{t}</button>
                ))}
                <button type="button" onClick={() => pickReason("", "Something else")}>Something else</button>
              </div>
            )}
            {!typing && step === "wish" && (
              <div className="quick">
                {WISHES.filter(([v]) => v).map(([v, t]) => (
                  <button type="button" key={v} onClick={() => pickWish(v, t)}>{t}</button>
                ))}
                <button type="button" onClick={() => pickWish("", "Not sure, you suggest")}>Not sure</button>
                <button type="button" className="human" onClick={() => pickWish(HUMAN, "Talk to a person")}>
                  Talk to a person
                </button>
              </div>
            )}
            {step === "done" && caseId && (
              <div className="quick">
                <Link href={`/cases/${caseId}`} className="cta primary">Open my return →</Link>
                <button type="button" onClick={restart}>Help with another item</button>
              </div>
            )}
            <div ref={end} />
          </div>

          {step === "details" && (
            <form className="support-input" onSubmit={sendDetails}>
              <input aria-label="Your message" placeholder="e.g. The strap broke after two days"
                value={text} onChange={(e) => setText(e.target.value)} autoFocus />
              <button className="primary" type="submit" disabled={!text.trim()}>Send</button>
            </form>
          )}
        </section>
      )}
    </>
  );
}
