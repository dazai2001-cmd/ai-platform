"use client";

import { useId, useState } from "react";
import { ArrowRight, MessageCircleQuestion } from "lucide-react";
import clsx from "clsx";
import type { LocalQuestion } from "@/lib/api";
import WorkspaceModal from "./WorkspaceModal";

export default function ClarificationDialog({ question, query, onAnswer, onClose, error }: {
  question: LocalQuestion; query?: string; onAnswer: (answer: string) => void; onClose: () => void; error?: string;
}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [details, setDetails] = useState("");
  const formId = useId();
  const groups = question.fields?.length ? question.fields : question.options?.length
    ? [{ id: "answer", label: "Choose an answer", options: question.options }] : [];
  const answer = [
    ...groups.flatMap((field) => selected[field.id] ? [field.id === "answer" ? selected[field.id] : `${field.label}: ${selected[field.id]}`] : []),
    details.trim(),
  ].filter(Boolean).join(". ");
  const draft = question.draft;
  const draftSize = draft?.width && draft?.height ? `${draft.width} × ${draft.height}` : null;

  return <WorkspaceModal title="A quick clarification" onClose={onClose} footer={
    <div className="flex items-center justify-end gap-3">
      <button type="button" onClick={onClose} className="rounded-md px-3 py-2 text-sm text-muted hover:bg-soft">Answer later</button>
      <button type="submit" form={formId} disabled={!answer || answer.length > 2000} className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-40">
        Continue <ArrowRight size={15} />
      </button>
    </div>
  }>
    <form id={formId} onSubmit={(event) => { event.preventDefault(); if (answer && answer.length <= 2000) onAnswer(answer); }} className="space-y-5">
      <div className="flex items-start gap-3">
        <span className="rounded-lg bg-brand/10 p-2 text-brand-ink"><MessageCircleQuestion size={22} /></span>
        <div className="min-w-0">
          <p className="font-medium leading-6">{question.question}</p>
          <p className="mt-1 text-xs leading-5 text-muted">Your task is paused while you choose. You can also write your own answer.</p>
        </div>
      </div>
      {query && <p className="line-clamp-2 rounded-md bg-soft p-3 text-xs leading-5 text-muted">{query}</p>}
      {draft && <div className="rounded-lg border border-line-soft bg-soft/50 p-3 text-sm">
        <p className="mb-2 text-xs font-medium text-muted">Proposed generation</p>
        {typeof draft.prompt === "string" && <p className="mb-2 leading-5">{draft.prompt}</p>}
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
          {typeof draft.style === "string" && <span>Style: {draft.style}</span>}
          {draftSize && <span>Size: {draftSize}</span>}
          {typeof draft.seconds === "number" && <span>{draft.seconds} seconds</span>}
          {typeof draft.fps === "number" && <span>{draft.fps} fps</span>}
        </div>
      </div>}
      {groups.map((field, fieldIndex) => <fieldset key={field.id} className="space-y-2">
        <legend className="mb-2 text-sm font-medium">{field.label}</legend>
        <div className="flex flex-wrap gap-2">
          {field.options.map((option, index) => <button key={option} type="button" aria-pressed={selected[field.id] === option} data-autofocus={fieldIndex === 0 && index === 0 ? true : undefined}
            onClick={() => setSelected((current) => ({ ...current, [field.id]: current[field.id] === option ? "" : option }))}
            className={clsx("rounded-lg border px-3 py-2 text-sm transition", selected[field.id] === option
              ? "border-brand bg-brand/10 text-brand-ink" : "border-line-soft bg-soft/30 text-ink-subtle hover:border-brand/40 hover:bg-soft")}>
            {option}
          </button>)}
        </div>
      </fieldset>)}
      <label className="block text-sm font-medium">{groups.length ? "Your own answer or extra details" : "Your answer"}
        <textarea data-autofocus={groups.length ? undefined : true} className="app-input mt-2 w-full resize-none rounded-lg px-3 py-2 font-normal" aria-label="Clarification answer"
          rows={3} maxLength={2000} placeholder="Tell me what you have in mind…" value={details} onChange={(event) => setDetails(event.target.value)} />
      </label>
      {error && <p role="alert" className="text-sm text-danger-ink">{error}</p>}
      {answer.length > 2000 && <p role="alert" className="text-sm text-danger-ink">Please shorten your answer to 2,000 characters.</p>}
    </form>
  </WorkspaceModal>;
}
