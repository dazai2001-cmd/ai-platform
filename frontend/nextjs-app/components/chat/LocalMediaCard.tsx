"use client";

import { useEffect, useState } from "react";
import { api, type LocalMediaJob } from "@/lib/api";

export default function LocalMediaCard({ initialJob, watch = true }: { initialJob: LocalMediaJob; watch?: boolean }) {
  const [job, setJob] = useState(initialJob);
  const [error, setError] = useState("");
  useEffect(() => setJob(initialJob), [initialJob]);
  useEffect(() => {
    if (!watch || !["queued", "running"].includes(job.status)) return;
    let active = true;
    const timer = setTimeout(() => {
      api.localMediaJob(job.id).then((updated) => { if (active) { setJob(updated); setError(""); } })
        .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "Could not update generation."); });
    }, 2000);
    return () => { active = false; clearTimeout(timer); };
  }, [job, error, watch]);

  return <div className="mt-3 rounded-md border border-line-soft bg-soft/40 p-3">
    <div className="flex items-center justify-between gap-3 text-sm">
      <span className="font-medium">{job.kind === "image" ? "Image" : "Video"} · {job.spec.model_id}</span>
      <span className="text-muted" aria-live="polite">{job.status}</span>
    </div>
    <p className="mt-1 text-xs text-muted">{job.spec.width} × {job.spec.height} · {job.spec.style}</p>
    {["queued", "running"].includes(job.status) && <>
      <progress className="mt-3 w-full accent-red-500" max={100} value={job.progress} aria-label="Generation progress" />
      <p className="text-xs text-muted">{job.message}</p>
      <button type="button" className="mt-2 text-xs text-danger-ink" onClick={() => {
        api.cancelLocalMedia(job.id).then(setJob).catch((cause) => setError(cause.message));
      }}>Cancel generation</button>
    </>}
    {job.error && <p role="alert" className="mt-2 text-sm text-danger-ink">{job.error}</p>}
    {error && <div className="mt-2 text-sm text-danger-ink"><p role="alert">{error}</p>
      <button type="button" className="mt-1 text-xs" onClick={() => {
        api.localMediaJob(job.id).then((updated) => { setJob(updated); setError(""); }).catch((cause) => setError(cause.message));
      }}>Retry generation status</button></div>}
    {job.status === "succeeded" && job.artifact_url && <div className="mt-3">
      {job.kind === "image"
        ? <img src={api.localArtifactUrl(job.artifact_url)} alt={job.spec.prompt} className="max-h-96 w-full rounded object-contain" />
        : <video src={api.localArtifactUrl(job.artifact_url)} controls preload="metadata" className="max-h-96 w-full rounded" />}
      <a href={api.localArtifactUrl(job.artifact_url)} download className="mt-2 inline-block text-xs text-brand-ink">Download {job.kind}</a>
    </div>}
  </div>;
}
