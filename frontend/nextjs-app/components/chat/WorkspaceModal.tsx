"use client";

import { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

export default function WorkspaceModal({ title, children, onClose, wide = false, footer }: {
  title: string; children: React.ReactNode; onClose: () => void; wide?: boolean; footer?: React.ReactNode;
}) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const panel = panelRef.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]',
    ) || []);
    (panel?.querySelector<HTMLElement>("[data-autofocus]") || panel)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); closeRef.current(); }
      if (event.key !== "Tab") return;
      const items = focusable();
      const first = items[0];
      const last = items[items.length - 1];
      if (!first) { event.preventDefault(); panel?.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel)) {
        event.preventDefault(); first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKey);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-3 backdrop-blur-sm sm:p-6">
      <div ref={panelRef} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
        className={`soft-fade-in flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-xl border border-line-soft bg-panel text-ink shadow-2xl outline-none ${wide ? "max-w-4xl" : "max-w-xl"}`}>
        <div className="flex items-center justify-between gap-3 border-b border-line-soft px-5 py-4">
          <h2 id={titleId} className="text-base font-semibold">{title}</h2>
          <button type="button" onClick={onClose} aria-label="Close dialog" className="rounded-md p-1.5 text-muted hover:bg-soft hover:text-ink"><X size={18} /></button>
        </div>
        <div className="overflow-y-auto p-5">{children}</div>
        {footer && <div className="shrink-0 border-t border-line-soft bg-panel px-5 py-4">{footer}</div>}
      </div>
    </div>, document.body,
  );
}
