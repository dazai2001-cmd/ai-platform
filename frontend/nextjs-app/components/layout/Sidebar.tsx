"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart2, Brain, BriefcaseBusiness, Files, LogOut, MessageSquareText, Settings, SquareStack, UserCircle } from "lucide-react";
import clsx from "clsx";
import { useAuth } from "@/lib/auth";

const nav = [
  { href: "/chat", label: "Chat", icon: MessageSquareText, desc: "General" },
  { href: "/brain", label: "Brain", icon: Brain, desc: "Knowledge" },
  { href: "/documents", label: "Docs", icon: Files, desc: "Library" },
  { href: "/career", label: "Career", icon: BriefcaseBusiness, desc: "CV match" },
  { href: "/dashboard", label: "BI", icon: BarChart2, desc: "Datasets" },
  { href: "/memory", label: "Memory", icon: SquareStack, desc: "Context" },
  { href: "/analytics", label: "Analytics", icon: Activity, desc: "Usage" },
  { href: "/settings", label: "Settings", icon: Settings, desc: "Models" },
];

export default function Sidebar() {
  const path = usePathname();
  const { authRequired, loading, logout, user } = useAuth();

  return (
    <aside className="sticky top-0 z-20 border-b border-slate-800/70 bg-slate-950/86 px-3 py-3 shadow-2xl shadow-slate-950/20 backdrop-blur-xl lg:h-screen lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r lg:px-4 lg:py-6">
      <div className="mb-3 flex items-center justify-between gap-3 px-1 lg:mb-8 lg:block lg:px-2">
        <div>
          <div className="text-base font-semibold tracking-normal text-white lg:text-xl">AI Platform</div>
          <div className="text-xs text-slate-500">Local Ollama workspace</div>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-400/25 bg-emerald-400/10 px-2 py-1 text-xs text-emerald-200 lg:mt-3 lg:inline-flex">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-300 shadow-[0_0_12px_rgba(110,231,183,0.9)]" />
          Local
        </span>
      </div>

      <nav className="grid grid-cols-4 gap-1 sm:grid-cols-8 lg:flex lg:flex-col">
        {nav.map(({ href, label, icon: Icon, desc }) => {
          const active = path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "group relative flex min-h-14 flex-col items-center justify-center gap-1 rounded-md px-2 py-2 text-center transition duration-150 ease-out hover:-translate-y-0.5 active:translate-y-0 lg:min-h-0 lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:py-2.5 lg:text-left",
                active
                  ? "border border-cyan-300/20 bg-cyan-400/14 text-white shadow-lg shadow-cyan-950/25"
                  : "border border-transparent text-slate-400 hover:border-slate-700/70 hover:bg-slate-900/82 hover:text-slate-100"
              )}
            >
              <span
                className={clsx(
                  "grid h-8 w-8 place-items-center rounded-md transition lg:h-7 lg:w-7",
                  active ? "bg-cyan-300/15 text-cyan-200" : "bg-slate-900/60 text-slate-500 group-hover:text-slate-200"
                )}
              >
                <Icon size={17} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium lg:text-sm">{label}</span>
                <span className={clsx("hidden text-xs lg:block", active ? "text-cyan-100/75" : "text-slate-600")}>
                  {desc}
                </span>
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-3 border-t border-slate-800/70 pt-3 lg:mt-6">
        {user ? (
          <div className="flex items-center gap-2 rounded-md border border-slate-800 bg-slate-900/65 px-2 py-2 text-xs text-slate-300">
            <UserCircle size={18} className="shrink-0 text-cyan-200" />
            <span className="min-w-0 flex-1 truncate">{user.email}</span>
            <button
              type="button"
              onClick={logout}
              className="grid h-7 w-7 place-items-center rounded-md text-slate-500 transition hover:bg-slate-800 hover:text-white"
              title="Sign out"
            >
              <LogOut size={15} />
            </button>
          </div>
        ) : authRequired && !loading ? (
          <Link
            href="/auth"
            className="flex items-center justify-center gap-2 rounded-md border border-cyan-300/20 bg-cyan-400/12 px-3 py-2 text-sm text-cyan-100 transition hover:bg-cyan-400/18"
          >
            <UserCircle size={16} />
            Sign in
          </Link>
        ) : null}
      </div>
    </aside>
  );
}
