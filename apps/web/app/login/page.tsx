"use client";

import type { ConfirmationResult } from "firebase/auth";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, post, saveTokens } from "../../lib/api";
import { confirmCode, firebaseEnabled, sendCode } from "../../lib/firebase";

type Session = { access_token: string; refresh_token: string; profile_complete?: boolean };

/** E.164 for India: accepts "98765 43210", "+91 98765 43210", "919876543210". */
function normalise(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  if (digits.length === 10) return `+91${digits}`;
  if (digits.length === 12 && digits.startsWith("91")) return `+${digits}`;
  return phone.trim();
}

export default function CustomerLogin() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [devCode, setDevCode] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmationResult | null>(null);
  const [error, setError] = useState("");

  function finish(session: Session) {
    saveTokens("customer", session);
    router.push(session.profile_complete === false ? "/profile" : "/orders");
  }

  async function requestCode(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (firebaseEnabled) {
        setConfirmation(await sendCode(normalise(phone), "send-code"));
      } else {
        // Development fallback when Firebase is not configured.
        const r = await post<{ dev_code?: string }>(null, "/auth/otp/request", { phone: normalise(phone) });
        setDevCode(r.dev_code ?? null);
      }
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send the code. Check the number and try again.");
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (firebaseEnabled && confirmation) {
        const idToken = await confirmCode(confirmation, code);
        finish(await post<Session>(null, "/auth/firebase", { id_token: idToken }));
      } else {
        finish(await post<Session>(null, "/auth/otp/verify", { phone: normalise(phone), code }));
      }
    } catch {
      setError("That code did not work. Check it and try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" style={{ maxWidth: 420 }}>
      <h1>Sign in or create an account</h1>
      {!sent ? (
        <form onSubmit={requestCode}>
          <label htmlFor="phone">Mobile number</label>
          <input id="phone" inputMode="tel" autoComplete="tel" placeholder="98765 43210"
            value={phone} onChange={(e) => setPhone(e.target.value)} required />
          <p className="small muted">We will send a 6-digit code by SMS. New here? Your account is created when you verify.</p>
          <button id="send-code" className="primary" type="submit" disabled={busy}>Send code</button>
        </form>
      ) : (
        <form onSubmit={verify}>
          <label htmlFor="code">6-digit code</label>
          <input id="code" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}"
            maxLength={6} value={code} onChange={(e) => setCode(e.target.value)} required />
          {devCode && (
            <p className="banner" data-testid="dev-code">
              Development mode (Firebase not configured): your code is <strong>{devCode}</strong>
            </p>
          )}
          <div className="row">
            <button className="primary" type="submit" disabled={busy}>Verify</button>
            <button type="button" onClick={() => { setSent(false); setCode(""); }}>Change number</button>
          </div>
        </form>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
