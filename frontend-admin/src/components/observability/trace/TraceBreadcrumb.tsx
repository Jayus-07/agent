"use client";

import Link from "next/link";

interface Crumb {
  label: string;
  href?: string;
}

export default function TraceBreadcrumb({ crumbs }: { crumbs: Crumb[] }) {
  return (
    <nav className="flex items-center gap-1 text-xs text-slate-500">
      {crumbs.map((c, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={i} className="flex items-center gap-1">
            {c.href && !isLast ? (
              <Link href={c.href} className="hover:text-violet-600 transition-colors">
                {c.label}
              </Link>
            ) : (
              <span className={isLast ? "text-slate-700 font-medium" : ""}>{c.label}</span>
            )}
            {!isLast && <span className="text-slate-300">/</span>}
          </span>
        );
      })}
    </nav>
  );
}