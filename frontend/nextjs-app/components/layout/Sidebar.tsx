"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Activity, BarChart2, Brain, BriefcaseBusiness, Files, LogOut, MessageSquareText, Settings, SquareStack, UserCircle } from "lucide-react";
import clsx from "clsx";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { useEffect, useState } from "react";

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
  const [runtime, setRuntime] = useState("local");

  useEffect(() => {
    let cancelled = false;
    api.health()
      .then((health) => {
        if (!cancelled) setRuntime(health.runtime || (health.cloud_models ? "cloud" : "local"));
      })
      .catch(() => {
        if (!cancelled) setRuntime(process.env.NEXT_PUBLIC_AUTH_REQUIRED === "true" ? "cloud" : "local");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const isCloud = runtime === "cloud";

  if (path.startsWith("/auth")) return null;

  return (
    <aside className="sticky top-0 z-20 border-b border-line-soft bg-panel px-3 py-3 lg:h-dvh lg:w-64 lg:shrink-0 lg:border-b-0 lg:border-r lg:px-4 lg:py-6">
      <div className="mb-3 flex items-center justify-between gap-3 px-1 lg:mb-8 lg:block lg:px-2">
        <div className="flex items-center gap-3">
          <span className="h-9 w-1 rounded-sm bg-brand" aria-hidden="true" />
          <div>
            <div className="text-base font-semibold text-ink lg:text-xl">AI Platform</div>
            <div className="text-xs text-muted">{isCloud ? "Cloud AI workspace" : "Local Ollama workspace"}</div>
          </div>
        </div>
        <span
          className={clsx(
            "inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-xs lg:mt-3 lg:inline-flex",
            isCloud
              ? "border-analytic/30 bg-analytic/10 text-analytic"
              : "border-success/25 bg-success/10 text-success-ink"
          )}
        >
          <span
            className={clsx(
              "h-1.5 w-1.5 rounded-full",
              isCloud ? "bg-analytic" : "bg-success"
            )}
          />
          {isCloud ? "Cloud" : "Local"}
        </span>
      </div>

      <nav className="nav-scroll flex gap-1 overflow-x-auto pb-1 sm:grid sm:grid-cols-8 sm:overflow-visible sm:pb-0 lg:flex lg:flex-col">
        {nav.map(({ href, label, icon: Icon, desc }) => {
          const active = path.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={clsx(
                "group relative flex min-h-14 min-w-20 flex-col items-center justify-center gap-1 rounded-md border px-2 py-2 text-center transition duration-150 sm:min-w-0 lg:min-h-0 lg:flex-row lg:justify-start lg:gap-3 lg:px-3 lg:py-2.5 lg:text-left",
                active
                  ? "border-brand/30 bg-brand-soft text-ink"
                  : "border-transparent text-muted hover:border-line-soft hover:bg-soft hover:text-ink"
              )}
            >
              {active && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-brand" aria-hidden="true" />}
              <span
                className={clsx(
                  "grid h-8 w-8 place-items-center rounded-md transition lg:h-7 lg:w-7",
                  active ? "bg-brand text-white" : "bg-soft text-muted group-hover:text-ink"
                )}
              >
                <Icon size={17} />
              </span>
              <span className="min-w-0">
                <span className="block truncate text-xs font-medium lg:text-sm">{label}</span>
                <span className={clsx("hidden text-xs lg:block", active ? "text-brand-ink/75" : "text-muted-soft")}>
                  {desc}
                </span>
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-3 border-t border-line-soft pt-3 lg:mt-6">
        {user ? (
          <div className="flex items-center gap-2 rounded-md border border-line-soft bg-soft/55 px-2 py-2 text-xs text-ink-subtle">
            <UserCircle size={18} className="shrink-0 text-analytic" />
            <span className="min-w-0 flex-1 truncate">{user.email}</span>
            <button
              type="button"
              onClick={logout}
              className="grid h-7 w-7 place-items-center rounded-md text-muted transition hover:bg-soft hover:text-ink"
              title="Sign out"
            >
              <LogOut size={15} />
            </button>
          </div>
        ) : authRequired && !loading ? (
          <Link
            href="/auth"
            className="flex items-center justify-center gap-2 rounded-md bg-brand px-3 py-2 text-sm font-semibold text-white transition hover:bg-brand-hover"
          >
            <UserCircle size={16} />
            Sign in
          </Link>
        ) : null}
      </div>
    </aside>
  );
}
