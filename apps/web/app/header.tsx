"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { signedIn } from "../lib/api";

type NavLink = { href: string; label: string };

const CUSTOMER: NavLink[] = [
  { href: "/orders", label: "My orders" },
  { href: "/profile", label: "My details" },
];
const TEAM: NavLink[] = [
  { href: "/console", label: "Support console" },
  { href: "/analytics", label: "Analytics" },
  { href: "/admin", label: "Admin" },
];

export function Logo() {
  return (
    <span className="logo" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4"
        strokeLinecap="round" strokeLinejoin="round">
        <path d="M9 14 4 9l5-5" />
        <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />
      </svg>
    </span>
  );
}

/** Links depend on who is signed in on this device: customers see their orders, staff see the
 *  console; signed-out visitors only see the two sign-in entry points. */
export function Header() {
  const path = usePathname() || "/";
  const [who, setWho] = useState<{ customer: boolean; staff: boolean } | null>(null);

  useEffect(() => {
    const read = () => setWho({ customer: signedIn("customer"), staff: signedIn("staff") });
    read();
    window.addEventListener("storage", read);
    return () => window.removeEventListener("storage", read);
  }, [path]);

  const link = (l: NavLink) => {
    const active = path === l.href || path.startsWith(l.href + "/");
    return (
      <Link key={l.href} href={l.href} aria-current={active ? "page" : undefined}>
        {l.label}
      </Link>
    );
  };

  return (
    <header className="top">
      <Link href="/" className="brand">
        <Logo />
        Vapsi
      </Link>
      <nav aria-label="Main">
        {who?.customer && CUSTOMER.map(link)}
        {who?.customer && who.staff && <span className="nav-sep" aria-hidden="true" />}
        {who?.staff && TEAM.map(link)}
      </nav>
      {who && !(who.customer && who.staff) && (
        <div className="nav-actions">
          {!who.staff && (
            <Link href="/console/login" className="nav-ghost">Staff sign in</Link>
          )}
          {!who.customer && (
            <Link href="/login" className="nav-cta">Sign in</Link>
          )}
        </div>
      )}
    </header>
  );
}
