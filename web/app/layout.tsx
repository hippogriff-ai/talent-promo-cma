import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "talent·promo coach",
  description: "Resume career-coach agent — research, discovery interview, grounded drafts.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/" className="wordmark">
            talent·promo<em>coach</em>
          </Link>
          <nav className="site-nav">
            <Link href="/">Runs</Link>
            <Link href="/memory">Memory</Link>
          </nav>
        </header>
        <main className="site-main">{children}</main>
      </body>
    </html>
  );
}
