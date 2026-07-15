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
      <div className="w-full max-w-md rounded-lg border border-line-soft bg-panel p-6">
        <div className="mb-6">
          <div className="mb-3 grid h-11 w-11 place-items-center rounded-md bg-brand/12 text-brand-ink">
            <KeyRound size={22} />
          </div>
          <h1 className="text-2xl font-semibold text-ink">{mode === "login" ? "Sign in" : "Create account"}</h1>
          <p className="mt-1 text-sm text-muted">
            {mode === "login"
              ? "Use your verified email to open the workspace."
              : "Create a temporary test account—no real inbox is required right now."}
          </p>
        </div>

        <div className="mb-5 grid grid-cols-2 rounded-md border border-line-soft bg-panel/70 p-1">
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
                mode === item ? "bg-ink text-white" : "text-muted hover:text-ink"
              }`}
            >
              {item === "login" ? "Login" : "Signup"}
            </button>
          ))}
        </div>

        {mode === "signup" && (
          <div className="mb-5 rounded-md border border-analytic/25 bg-analytic/10 px-3 py-3 text-sm text-ink-subtle">
            <p className="font-medium text-ink">Made-up emails are allowed for testing</p>
            <p className="mt-1">
              Use any valid-looking address, such as <span className="font-medium text-ink">demo@example.com</span>.
              If a verification link appears after sign-up, open it before logging in.
            </p>
          </div>
        )}

        <form onSubmit={submit} className="space-y-4">
          <label className="block">
            <span className="text-sm text-ink-subtle">Email</span>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              type="email"
              autoComplete="email"
              required
              className="mt-1 w-full rounded-md border border-line-soft bg-panel px-3 py-2 text-sm text-ink outline-none transition focus:border-analytic/70"
            />
          </label>

          <label className="block">
            <span className="text-sm text-ink-subtle">Password</span>
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              required
              minLength={8}
              className="mt-1 w-full rounded-md border border-line-soft bg-panel px-3 py-2 text-sm text-ink outline-none transition focus:border-analytic/70"
            />
          </label>

          {error && (
            <div className="rounded-md border border-danger/25 bg-danger/10 px-3 py-2 text-sm text-danger-ink">
              {error}
            </div>
          )}

          {(verificationSent || verificationUrl) && (
            <div className="rounded-md border border-success/25 bg-success/10 px-3 py-3 text-sm text-success-ink">
              <div className="mb-2 flex items-center gap-2 font-medium">
                <MailCheck size={16} />
                {verificationSent ? "Verification email sent" : "Verification link created"}
              </div>
              <p className="text-success-ink/90">
                {verificationMessage || (verificationSent ? "Check your inbox before signing in." : "Open this link to verify your email.")}
              </p>
              {verificationUrl && (
                <Link href={verificationUrl} className="mt-2 block break-all text-brand-ink underline underline-offset-4">
                  {verificationUrl}
                </Link>
              )}
            </div>
          )}

          <button
            type="submit"
            disabled={loading}
            className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-brand px-4 py-2.5 text-sm font-medium text-white transition hover:bg-brand-hover disabled:cursor-not-allowed disabled:opacity-60"
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
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <AuthPageContent />
    </Suspense>
  );
}
