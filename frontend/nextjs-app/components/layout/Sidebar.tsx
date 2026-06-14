"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart2, Brain, Settings } from "lucide-react";
import clsx from "clsx";

const nav = [
  { href: "/brain", label: "Brain", icon: Brain, desc: "Knowledge" },
  { href: "/dashboard", label: "BI", icon: BarChart2, desc: "Datasets" },
  { href: "/analytics", label: "Analytics", icon: Activity, desc: "Usage" },
  { href: "/settings", label: "Status", icon: Settings, desc: "System" },
];

export default function Sidebar() {
  const path = usePathname();

  return (
    <aside className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/95 px-3 py-3 backdrop-blur lg:h-screen lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r lg:px-4 lg:py-6">
      <div className="mb-3 flex items-center justify-between gap-3 px-1 lg:mb-8 lg:block lg:px-2">
        <div>
          <div className="text-base font-semibold tracking-normal text-white lg:text-xl">AI Platform</div>
          <div className="text-xs text-slate-500">Local Ollama workspace</div>
        </div>
        <span className="rounded-full border border-cyan-400/30 bg-cyan-400/10 px-2 py-1 text-xs text-cyan-200 lg:mt-3 lg:inline-block">
          Local
        </span>
      </div>

      <nav className="grid grid-cols-4 gap-1 lg:flex lg:flex-col">
        {nav.map(({ href, label, icon: Icon, desc }) => {
          const active = path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "flex min-h-14 flex-col items-center justify-center gap-1 rounded-md px-2 py-2 text-center transition lg:min-h-0 lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:py-2.5 lg:text-left",
                active
                  ? "bg-indigo-500 text-white shadow-lg shadow-indigo-950/30"
                  : "text-slate-400 hover:bg-slate-900 hover:text-slate-100"
              )}
            >
              <Icon size={18} />
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium lg:text-sm">{label}</span>
                <span className={clsx("hidden text-xs lg:block", active ? "text-indigo-100" : "text-slate-600")}>
                  {desc}
                </span>
              </span>
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
