import Link from "next/link";

const ROLES = [
  {
    title: "Customers",
    text: "Start a return in plain language (English, Hindi, Hinglish, Telugu…), upload a photo if needed, and track it to the refund.",
    href: "/login",
    cta: "Sign in with your phone",
    bg: "var(--info-soft)",
    fg: "var(--brand)",
    icon: <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z" />,
  },
  {
    title: "Support team",
    text: "Approval and escalation queues with a full handoff: summary, decision record, policy clauses, evidence and risk signals.",
    href: "/console",
    cta: "Open the console",
    bg: "var(--ok-soft)",
    fg: "var(--ok)",
    icon: <path d="M3 11a9 9 0 0 1 18 0v6a2 2 0 0 1-2 2h-1v-7h3M3 11v6a2 2 0 0 0 2 2h1v-7H3" />,
  },
  {
    title: "Admins",
    text: "Policies, decision thresholds, the procedural graph and staff accounts, all versioned and audited.",
    href: "/admin",
    cta: "Open admin",
    bg: "var(--accent-soft)",
    fg: "var(--warn)",
    icon: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />,
  },
];

const STEPS = [
  ["Understand", "Reads the request and asks only for what is missing."],
  ["Verify", "Checks the order and the return policy, and cites the clause."],
  ["Decide", "Picks the best resolution; risky cases go to a person."],
  ["Resolve", "Books the pickup, issues the refund and keeps you updated."],
];

export default function Home() {
  return (
    <>
      <section className="hero">
        <span className="eyebrow">Autonomous return resolution</span>
        <h1>
          Returns, resolved <em>in minutes</em>
        </h1>
        <p className="lead">
          An AI agent that follows your return policy exactly, cites the clause behind every decision,
          and hands risky or high-value cases to a person.
        </p>
        <div className="row" style={{ justifyContent: "center" }}>
          <Link href="/login" className="cta primary">Start a return</Link>
          <Link href="/console" className="cta ghost">Support console</Link>
        </div>
      </section>

      <div className="grid">
        {ROLES.map((r) => (
          <section className="card role-card" key={r.title}>
            <span className="icon" style={{ background: r.bg, color: r.fg }}>
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                {r.icon}
              </svg>
            </span>
            <h2>{r.title}</h2>
            <p>{r.text}</p>
            <Link href={r.href}>{r.cta} →</Link>
          </section>
        ))}
      </div>

      <h2 className="section-title">How it works</h2>
      <div className="how">
        {STEPS.map(([title, text], i) => (
          <div className="step card" key={title}>
            <span className="num">{i + 1}</span>
            <div>
              <strong>{title}</strong>
              <span className="muted small">{text}</span>
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
