"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { post, saveTokens } from "../../../lib/api";

export default function StaffLogin() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      saveTokens("staff", await post("staff", "/auth/staff/login", { email, password }));
      router.push(new URLSearchParams(window.location.search).get("next") || "/console");
    } catch {
      setError("Email or password is incorrect.");
    }
  }

  return (
    <form className="card auth" onSubmit={submit}>
      <h1>Staff sign in</h1>
      <p className="lead">Support console, analytics and admin.</p>
      <label htmlFor="email">Work email</label>
      <input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
      <label htmlFor="password">Password</label>
      <input id="password" type="password" autoComplete="current-password" value={password}
        onChange={(e) => setPassword(e.target.value)} required />
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" type="submit">Sign in</button>
    </form>
  );
}
