"use client";

import { useId, useState } from "react";
import { WandSparkles } from "lucide-react";
import type { LocalCapabilities } from "@/lib/api";
import WorkspaceModal from "./WorkspaceModal";

export default function WorkspaceMediaDialog({ capabilities, onCreate, onClose }: {
  capabilities: LocalCapabilities; onCreate: (prompt: string) => void; onClose: () => void;
}) {
  const [kind, setKind] = useState<"image" | "video">("image");
  const [subject, setSubject] = useState("");
  const [style, setStyle] = useState("watercolor");
  const [shape, setShape] = useState("square");
  const [seconds, setSeconds] = useState(2);
  const formId = useId();
  const models = capabilities.models?.filter((model) => model.kind === kind) || [];
  const ready = models.some((model) => model.ready);
  const generate = () => {
    const sizes = kind === "image" ? { square: "512 by 512", landscape: "640 by 384", portrait: "384 by 640" }
      : { square: "512 by 512", landscape: "704 by 512", portrait: "512 by 704" };
    const prompt = `Create a ${kind}. Subject: ${subject.trim()}. Style: ${style.trim()}. Size: ${shape}, ${sizes[shape as keyof typeof sizes]}.` +
      (kind === "video" ? ` Duration: ${seconds} seconds. Frame rate: 25 fps.` : "");
    onCreate(prompt);
  };

  return <WorkspaceModal title="Create media" onClose={onClose} footer={
    <div className="flex justify-end gap-3">
      <button type="button" onClick={onClose} className="rounded-md px-3 py-2 text-sm text-muted hover:bg-soft">Cancel</button>
      <button type="submit" form={formId} disabled={!ready || !capabilities.checkpoint_ready || !subject.trim() || !style.trim()}
        className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white disabled:opacity-40"><WandSparkles size={16} /> Generate {kind}</button>
    </div>
  }>
    <form id={formId} className="space-y-4" onSubmit={(event) => { event.preventDefault(); if (ready && capabilities.checkpoint_ready && subject.trim() && style.trim()) generate(); }}>
      <p className="text-sm leading-6 text-muted">Describe your scene. Qwen will select an available model and share the result in this conversation.</p>
      <label className="block text-sm font-medium">Output
        <select className="app-input mt-1 w-full rounded-lg p-2" value={kind} onChange={(event) => setKind(event.target.value as "image" | "video")}>
          <option value="image">Image</option><option value="video">Video preview</option>
        </select>
      </label>
      <label className="block text-sm font-medium">Subject and scene
        <textarea data-autofocus className="app-input mt-1 w-full resize-none rounded-lg p-3" maxLength={1000} rows={3} value={subject}
          onChange={(event) => setSubject(event.target.value)} placeholder="A cat sitting by a window in the rain" />
      </label>
      <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-medium">Visual style<input className="app-input mt-1 w-full rounded-lg p-2" maxLength={120} value={style} onChange={(event) => setStyle(event.target.value)} /></label>
        <label className="text-sm font-medium">Shape<select className="app-input mt-1 w-full rounded-lg p-2" value={shape} onChange={(event) => setShape(event.target.value)}>
          <option value="square">Square</option><option value="landscape">Landscape</option><option value="portrait">Portrait</option>
        </select></label>
      </div>
      {kind === "video" && <div className="grid grid-cols-2 gap-3">
        <label className="text-sm font-medium">Duration (seconds)<input type="number" required min={1} max={6} step={1} className="app-input mt-1 w-full rounded-lg p-2" value={seconds} onChange={(event) => setSeconds(Number(event.target.value))} /></label>
        <p className="self-center text-sm text-muted">Silent preview · 25 fps</p>
      </div>}
      {kind === "video" && <p className="text-xs leading-5 text-muted">Experimental LTX video preview. Motion and shapes may be imperfect. Frame rounding can make the clip slightly longer than requested.</p>}
      <div className="rounded-lg border border-line-soft bg-soft/50 p-3">
        {models.map((model) => <p key={model.id} className="text-xs leading-5 text-muted">{model.id}: {model.reason} <a className="text-brand-ink" href={model.license_url} target="_blank" rel="noreferrer">Model license</a></p>)}
        {!ready && <p role="status" className="mt-1 text-xs text-warning-ink">No local {kind} model is ready yet.</p>}
        {!capabilities.checkpoint_ready && <p role="alert" className="mt-1 text-xs text-danger-ink">The local checkpoint dependency needs to be installed.</p>}
      </div>
    </form>
  </WorkspaceModal>;
}
