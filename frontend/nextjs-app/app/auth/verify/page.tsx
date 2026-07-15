"use client";

import { useEffect, useState } from "react";
import { Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle } from "lucide-react";
import { api } from "@/lib/api";

function VerifyEmailContent() {
  const search = useSearchParams();
  const token = search.get("token") || "";
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  const [message, setMessage] = useState("Checking your verification link...");

  useEffect(() => {
    async function verify() {
      if (!token) {
        setState("error");
        setMessage("Verification token is missing.");
        return;
      }
      try {
        await api.verifyEmail(token);
        setState("ok");
        setMessage("Your email is verified. You can sign in now.");
      } catch (e: any) {
        setState("error");
        setMessage(e.message || "Verification failed.");
      }
    }
    verify();
  }, [token]);

  const Icon = state === "ok" ? CheckCircle2 : XCircle;

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md rounded-lg border border-line-soft bg-panel p-6 text-center">
        <div className={`mx-auto mb-4 grid h-12 w-12 place-items-center rounded-md ${state === "ok" ? "bg-success/12 text-success-ink" : "bg-soft text-ink-subtle"}`}>
          <Icon size={24} />
        </div>
        <h1 className="text-xl font-semibold text-ink">Email verification</h1>
        <p className="mt-2 text-sm text-muted">{message}</p>
        <Link
          href="/auth"
          className="mt-6 inline-flex rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition hover:bg-brand-hover"
        >
          Go to sign in
        </Link>
      </div>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-canvas" />}>
      <VerifyEmailContent />
    </Suspense>
  );
}
