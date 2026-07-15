import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";
import { AuthProvider } from "@/lib/auth";

export const metadata: Metadata = {
  title: "AI Platform",
  description: "A focused workspace for AI, knowledge, analysis, and career tools",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <div className="min-h-screen bg-canvas text-ink lg:flex">
            <Sidebar />
            <main className="min-h-0 flex-1 overflow-hidden bg-canvas lg:min-h-dvh">
              {children}
            </main>
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
