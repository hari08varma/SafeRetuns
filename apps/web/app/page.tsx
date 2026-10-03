import Link from "next/link";

export default function Home() {
  return (
    <>
      <h1>Returns, resolved in minutes</h1>
      <p className="muted">
        An AI agent that follows your return policy exactly, cites the clause behind every decision,
        and hands risky or high-value cases to a person.
      </p>
      <div className="grid">
        <section className="card">
          <h2>Customers</h2>
          <p>Start a return in plain language (English, Hindi, Hinglish, Telugu…), upload a photo if
            needed, and track it to the refund.</p>
          <Link href="/login">Sign in with your phone</Link>
        </section>
        <section className="card">
          <h2>Support team</h2>
          <p>Approval and escalation queues with a full handoff: summary, decision record, policy
            clauses, evidence and risk signals.</p>
          <Link href="/console">Open the console</Link>
        </section>
        <section className="card">
          <h2>Admins</h2>
          <p>Policies, decision thresholds, the procedural graph and staff accounts.</p>
          <Link href="/admin">Open admin</Link>
        </section>
      </div>
    </>
  );
}
