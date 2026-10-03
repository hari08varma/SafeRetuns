import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Header } from "./header";
import "./globals.css";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });

export const metadata: Metadata = {
  title: "Vapsi",
  description: "Autonomous product return resolution",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={sans.variable}>
      <body>
        <a className="skip" href="#main">
          Skip to content
        </a>
        <Header />
        <main id="main">{children}</main>
        <footer className="site">
          <span>Vapsi · Returns, resolved.</span>
          <span>AI decisions follow the store&apos;s return policy. A person reviews risky cases.</span>
        </footer>
      </body>
    </html>
  );
}
