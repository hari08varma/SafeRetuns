"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const CUSTOMER = [
  { href: "/orders", label: "My orders" },
  { href: "/profile", label: "My details" },
];
const TEAM = [
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

export function Header() {
  const path = usePathname() || "/";
  const link = (l: { href: string; label: string }) => {
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
        {CUSTOMER.map(link)}
        <span className="nav-sep" aria-hidden="true" />
        {TEAM.map(link)}
      </nav>
    </header>
  );
}
