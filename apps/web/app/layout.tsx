import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "SafeReturns",
  description: "Autonomous product return resolution",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        <header className="top">
          <Link href="/" className="brand">
            SafeReturns
          </Link>
          <nav aria-label="Main">
            <Link href="/orders">My orders</Link>
            <Link href="/profile">My details</Link>
            <Link href="/console">Support console</Link>
            <Link href="/analytics">Analytics</Link>
            <Link href="/admin">Admin</Link>
          </nav>
        </header>
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
