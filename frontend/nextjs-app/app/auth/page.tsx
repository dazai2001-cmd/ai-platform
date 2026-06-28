"use client";

import { FormEvent, useState } from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { KeyRound, LogIn, MailCheck } from "lucide-react";
import { useAuth } from "@/lib/auth";

function AuthPageContent() {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [verificationUrl, setVerificationUrl] = useState("");
  const [verificationSent, setVerificationSent] = useState(false);
  const [verificationMessage, setVerificationMessage] = useState("");
  const { login, signup } = useAuth();
  const router = useRouter();
  const search = useSearchParams();
  const next = search.get("next") || "/chat";

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setVerificationUrl("");
    setVerificationSent(false);
    setVerificationMessage("");
    setLoading(true);
    try {
      if (mode === "signup") {
        const result = await signup(email, password);
        setVerificationUrl(result.verification_url || "");
        setVerificationSent(Boolean(result.verification_sent));
        setVerificationMessage(result.message || "");
      } else {
        await login(email, password);
        router.replace(next);
      }
    } catch (e: any) {
      setError(e.message || "Auth request failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md rounded-lg border border-slate-800 bg-slate-950/80 p-6 shadow-2xl shadow-slate-950/30">
        <div className="mb-6">
          <div className="mb-3 grid h-11 w-11 place-items-center rounded-md bg-cyan-400/12 text-cyan-200">
            <KeyRound size={22} />
          </div>
          <h1 className="text-2xl font-semibold text-white">{mode === "login" ? "Sign in" : "Create account"}</h1>
          <p className="mt-1 text-sm text-slate-400">
            {mode === "login" ? "Use your verified email to open the workspace." : "Verify your email before logging in."}
          </p>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-md border border-slate-800 bg-slate-900/70 p-1">
          {(["login", "signup"] as const).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => {
                setMode(item);
                setError("");
                setVerificationUrl("");
                setVerificationSent(false);
                setVerificationMessage("");
              }}
              className={`rounded px-3 py-2 text-sm transition ${
                mode === item ? "bg-cyan-400/15 text-cyan-100" : "text-slate-400 hover:text-white"
              }`}
            >
              {item === "login" ? "Login" : "Signup"}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-sm text-slate-300">Email</span>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              required
              className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-300/70"
            />
          </label>

          <label className="block">
            <span className="text-sm text-slate-300">Password</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              minLength={8}
              className="mt-1 w-full rounded-md border border-slate-800 bg-slate-900 px-3 py-2 text-sm text-white outline-none transition focus:border-cyan-300/70"
            />
          </label>

          {error && (
            <div className="rounded-md border border-rose-400/25 bg-rose-400/10 px-3 py-2 text-sm text-rose-100">
              {error}
            </div>
          )}

          {(verificationSent || verificationUrl) && (
            <div className="rounded-md border border-emerald-400/25 bg-emerald-400/10 px-3 py-3 text-sm text-emerald-100">
              <div className="mb-2 flex items-center gap-2 font-medium">
                <MailCheck size={16} />
                {verificationSent ? "Verification email sent" : "Verification link created"}
              </div>
              <p className="text-emerald-50/90">
                {verificationMessage || (verificationSent ? "Check your inbox before signing in." : "Open this link to verify your email.")}
              </p>
              {verificationUrl && (
                <Link href={verificationUrl} className="mt-2 block break-all text-cyan-100 underline underline-offset-4">
                  {verificationUrl}
                </Link>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-2.5 text-sm font-medium text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <LogIn size={16} />
            {loading ? "Working..." : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function AuthPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-950" />}>
      <AuthPageContent />
    </Suspense>
  );
}
