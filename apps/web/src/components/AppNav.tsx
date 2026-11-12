"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Command Center" },
  { href: "/markets", label: "Markets" },
  { href: "/strategies", label: "Strategies" },
  { href: "/journal", label: "Journal" },
  { href: "/settings", label: "Settings" },
];

/**
 * The app's only navigation. Deliberately short: five screens, all reachable
 * with one thumb on a phone.
 */
export function AppNav() {
  const pathname = usePathname();

  return (
    <nav className="flex items-center gap-1 px-3 h-10 border-b border-border bg-panel/60 overflow-x-auto shrink-0">
      <Link
        href="/"
        className="text-xs font-semibold tracking-tight mr-3 shrink-0"
      >
        TCC
      </Link>
      {LINKS.map((link) => {
        const active =
          link.href === "/"
            ? pathname === "/"
            : pathname === link.href || pathname.startsWith(`${link.href}/`);
        return (
          <Link
            key={link.href}
            href={link.href}
            className={`px-2.5 py-1 rounded text-[11px] whitespace-nowrap transition-colors ${
              active
                ? "bg-panel-2 text-foreground"
                : "text-muted hover:text-foreground"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
