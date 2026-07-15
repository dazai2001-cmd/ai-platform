"use client";

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";

type User = {
  id: string;
  email: string;
  email_verified: boolean;
  created_at: number;
};

type AuthContextValue = {
  user: User | null;
  loading: boolean;
  authRequired: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string) => Promise<any>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);
const AUTH_REQUIRED = process.env.NEXT_PUBLIC_AUTH_REQUIRED === "true";
const LEGACY_AUTH_STORAGE_KEY = "ai_platform_auth_token";

function clearLegacyBrowserToken() {
  try {
    window.localStorage.removeItem(LEGACY_AUTH_STORAGE_KEY);
  } catch {
    // Storage can be unavailable in hardened/private browser contexts.
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const pathname = usePathname();
  const router = useRouter();

  async function refresh() {
    setLoading(true);
    clearLegacyBrowserToken();
    try {
      const data = await api.me();
      setUser(data.user);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (!AUTH_REQUIRED || loading || user || pathname.startsWith("/auth")) return;
    router.replace(`/auth?next=${encodeURIComponent(pathname)}`);
  }, [loading, pathname, router, user]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    authRequired: AUTH_REQUIRED,
    async login(email: string, password: string) {
      const data = await api.login(email, password);
      clearLegacyBrowserToken();
      setUser(data.user);
    },
    async signup(email: string, password: string) {
      return api.signup(email, password);
    },
    async logout() {
      try {
        await api.logout();
      } finally {
        clearLegacyBrowserToken();
        setUser(null);
        if (AUTH_REQUIRED) router.replace("/auth");
      }
    },
    refresh,
  }), [loading, router, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
