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
        <div className="min-h-screen bg-slate-950 text-slate-100 lg:flex">
          <Sidebar />
          <main className="min-h-screen flex-1 overflow-hidden bg-[radial-gradient(circle_at_top_left,rgba(14,165,233,0.10),transparent_34%),linear-gradient(180deg,#020617,#0f172a)]">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
