import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/layout/Sidebar";

export const metadata: Metadata = {
  title: "AI Platform",
  description: "Your local AI workspace",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen text-slate-100 lg:flex">
          <Sidebar />
          <main className="min-h-screen flex-1 overflow-hidden bg-slate-950/55">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
