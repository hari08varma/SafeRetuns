"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, api } from "../../lib/api";

type Profile = { name: string; phone: string; email: string | null; address: string | null;
  city: string | null; pincode: string | null; profile_complete: boolean };

export default function ProfilePage() {
  const router = useRouter();
  const [p, setP] = useState<Profile | null>(null);
  const [form, setForm] = useState({ name: "", email: "", address: "", city: "", pincode: "" });
  const [error, setError] = useState("");

  useEffect(() => {
    api<Profile>("customer", "/me/profile").then((data) => {
      setP(data);
      setForm({
        name: data.name, email: data.email ?? "",
        address: (data.address ?? "").replace(/, [^,]* \d{6}$/, ""), city: data.city ?? "", pincode: data.pincode ?? "",
      });
    }).catch(() => setError("Could not load your profile."));
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("customer", "/me/profile", {
        method: "PUT",
        body: JSON.stringify({ ...form, email: form.email || null }),
      });
      router.push("/orders");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not save.");
    }
  }

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });
  if (!p) return <p className="muted">{error || "Loading…"}</p>;
  return (
    <form className="card" style={{ maxWidth: 520 }} onSubmit={save}>
      <h1>{p.profile_complete ? "Your details" : "Finish setting up your account"}</h1>
      <p className="small muted">Signed in as {p.phone}. We use your address to arrange pickups.</p>
      <label htmlFor="name">Full name</label>
      <input id="name" autoComplete="name" required value={form.name} onChange={set("name")} />
      <label htmlFor="email">Email (optional)</label>
      <input id="email" type="email" autoComplete="email" value={form.email} onChange={set("email")} />
      <label htmlFor="address">Pickup address</label>
      <input id="address" autoComplete="street-address" required minLength={5} value={form.address} onChange={set("address")} />
      <div className="grid">
        <div><label htmlFor="city">City</label>
          <input id="city" autoComplete="address-level2" required value={form.city} onChange={set("city")} /></div>
        <div><label htmlFor="pincode">PIN code</label>
          <input id="pincode" inputMode="numeric" pattern="[1-9][0-9]{5}" required value={form.pincode} onChange={set("pincode")} /></div>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary" type="submit" style={{ marginTop: 12 }}>Save</button>
    </form>
  );
}
