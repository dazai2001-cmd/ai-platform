from __future__ import annotations

import json
import re
import time
from typing import Any

from agents.bi_agent import bi_agent
from agents.general_agent import general_agent
from agents.rag_agent import rag_agent
from core.config.constants import TASK_BI, TASK_CAREER, TASK_GENERAL, TASK_MEMORY, TASK_RAG
from core.config.settings import settings
from infrastructure.llm.ollama_client import ollama
from services.analytics.analytics_service import analytics
from services.career.job_search_service import MIN_MATCH_SCORE, SEARCH_JOB_SOURCES, career_jobs
from services.memory.memory_service import memory
from services.settings.model_settings_service import model_settings


WORKSPACE_ACTIONS = {
    "rag_ask",
    "bi_ask",
    "general_chat",
    "memory_list",
    "memory_add_fact",
    "docs_list",
    "docs_preview",
    "docs_ingest_url",
    "note_ingest",
    "career_search",
    "career_score_all",
    "career_list_jobs",
    "settings_show",
    "analytics_summary",
    "open_page",
}

_ACTION_PROMPT = """
You are the workspace router for a multi-tool AI platform. Choose exactly one action.

Allowed actions:
- rag_ask: answer from uploaded documents/notes/URLs
- bi_ask: analyze uploaded datasets, charts, metrics, SQL-like questions
- general_chat: normal conversation or general knowledge
- memory_list: show saved memory facts or conversation memory
- memory_add_fact: save a fact the user explicitly asks you to remember
- docs_list: list uploaded/ingested documents
- docs_preview: preview or inspect a named document
- docs_ingest_url: ingest a public URL into Brain
- note_ingest: save plain text as a Brain note
- career_search: search jobs using saved CV/profile and criteria
- career_score_all: score found/unscored jobs against saved CV/profile
- career_list_jobs: list found/matched/saved/applied/skipped jobs
- settings_show: show selected models/provider settings
- analytics_summary: show usage, latency, query stats
- open_page: user mainly wants to open a specialist page

Return ONLY raw JSON with this schema:
{
  "action": "one allowed action",
  "confidence": 0.0-1.0,
  "arguments": {
    "url": "only for docs_ingest_url",
    "note": "only for note_ingest or memory_add_fact",
    "document": "optional document/source name",
    "page": "brain|documents|career|dashboard|memory|analytics|settings"
  }
}

Rules:
- Do not invent files, jobs, or datasets.
- Use career_* for jobs, CVs, resumes, applications, cover letters, and matching.
- Use docs_* for uploaded PDFs, URLs, notes, chunks, document library, and Brain files.
- Use rag_ask when the user asks a question that needs document content.
- Use bi_ask for dataset/CSV/Excel analysis.
- Use memory_add_fact only when the user explicitly says to remember/save a personal fact.
- Default to general_chat if unsure.

User request:
{query}
""".strip()


class WorkspaceRouter:
    def route(self, query: str, user_id: str = "local") -> dict[str, Any]:
        model = settings.ROUTER_MODEL
        priority = self._priority_route(query)
        if priority:
            priority["router_model"] = model
            priority["router_source"] = "rules"
            return priority

        try:
            response = ollama.generate(
                model,
                _ACTION_PROMPT.format(query=query),
                temperature=0,
                max_tokens=220,
                json_format=True,
            )
            parsed = self._parse_json(response)
            action = str(parsed.get("action") or "").strip()
            if action in WORKSPACE_ACTIONS:
                args = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}
                return {
                    "action": action,
                    "confidence": parsed.get("confidence", 0.75),
                    "arguments": args,
                    "router_model": model,
                    "router_source": "model",
                }
        except Exception:
            pass

        fallback = self._fallback_route(query)
        fallback["router_model"] = model
        fallback["router_source"] = "fallback"
        return fallback

    def handle(self, query: str, session_id: str, user_id: str = "local") -> dict[str, Any]:
        decision = self.route(query, user_id=user_id)
        action = decision["action"]
        args = decision.get("arguments") or {}
        t0 = time.monotonic()

        if action == "rag_ask":
            result = rag_agent.ask(query, session_id=session_id, model=model_settings.model_for(TASK_RAG, user_id=user_id), user_id=user_id)
            return self._with_workspace_meta(result, decision, TASK_RAG)

        if action == "bi_ask":
            result = bi_agent.ask(query, session_id=session_id, model=model_settings.model_for(TASK_BI, user_id=user_id), user_id=user_id)
            return self._with_workspace_meta(result, decision, TASK_BI)

        if action == "general_chat":
            result = general_agent.ask(query, session_id=session_id, model=model_settings.model_for(TASK_GENERAL, user_id=user_id), user_id=user_id)
            return self._with_workspace_meta(result, decision, TASK_GENERAL)

        if action == "memory_add_fact":
            note = str(args.get("note") or self._memory_fact_from_query(query)).strip()
            if not note:
                return self._tool_response("Tell me what to remember, and I will save it.", action, TASK_MEMORY, decision)
            fact = memory.add_fact(note, user_id=user_id)
            answer = f"Saved this to memory:\n\n- {fact['content']}"
            return self._tool_response(answer, action, TASK_MEMORY, decision, {"fact": fact})

        if action == "memory_list":
            facts = memory.facts(user_id=user_id)
            sessions = memory.session_summaries(user_id=user_id)
            return self._tool_response(self._format_memory(facts, sessions), action, TASK_MEMORY, decision, {"facts": facts, "sessions": sessions})

        if action == "docs_list":
            docs = rag_agent.documents(user_id=user_id)
            return self._tool_response(self._format_documents(docs), action, "documents", decision, {"documents": docs})

        if action == "docs_preview":
            docs = rag_agent.documents(user_id=user_id)
            source = self._match_document_source(str(args.get("document") or query), docs)
            if not source:
                return self._tool_response("I could not find a matching document. Open Documents to pick one, or ask me to list documents.", action, "documents", decision)
            preview = rag_agent.document_preview(source, user_id=user_id)
            return self._tool_response(self._format_document_preview(preview), action, "documents", decision, {"preview": preview})

        if action == "docs_ingest_url":
            url = str(args.get("url") or self._extract_url(query)).strip()
            if not url:
                return self._tool_response("Send me a public URL and I can ingest it into Brain.", action, TASK_RAG, decision)
            chunks = rag_agent.ingest_url(url, user_id=user_id)
            return self._tool_response(f"Ingested URL into Brain:\n\n{url}\n\nChunks created: {chunks}", action, TASK_RAG, decision, {"url": url, "chunks": chunks})

        if action == "note_ingest":
            note = str(args.get("note") or self._note_from_query(query)).strip()
            if not note:
                return self._tool_response("Send me the note text you want to save into Brain.", action, TASK_RAG, decision)
            source = f"workspace-note-{int(time.time())}.txt"
            chunks = rag_agent.ingest_text(note, source=source, user_id=user_id)
            return self._tool_response(f"Saved note into Brain as `{source}`.\n\nChunks created: {chunks}", action, TASK_RAG, decision, {"source": source, "chunks": chunks})

        if action == "career_search":
            profile = career_jobs.profile(user_id=user_id)
            cv_text = str(profile.get("cv_text") or "").strip()
            preferences_override = self._career_search_preferences_from_query(query)
            result = career_jobs.search_jobs(
                cv_text,
                limit=50,
                user_id=user_id,
                preferences_override=preferences_override,
            )
            if preferences_override:
                result["workspace_criteria"] = preferences_override
            return self._tool_response(self._format_career_search(result), action, TASK_CAREER, decision, result)

        if action == "career_score_all":
            profile = career_jobs.profile(user_id=user_id)
            cv_text = str(profile.get("cv_text") or "").strip()
            if not cv_text:
                return self._tool_response("Save your CV/profile in Career first, then I can score jobs against it.", action, TASK_CAREER, decision)
            jobs = career_jobs.list_jobs(user_id=user_id)
            ids = [
                job["id"]
                for job in jobs
                if job.get("source") in SEARCH_JOB_SOURCES
                and not isinstance(job.get("fit_score"), (int, float))
                and job.get("status") not in {"applied", "skipped"}
            ]
            if not ids:
                return self._tool_response("There are no unscored found jobs right now. Run a job search first.", action, TASK_CAREER, decision)
            batch = career_jobs.create_score_batch(cv_text=cv_text, job_ids=ids, user_id=user_id)
            answer = f"Started background scoring for {batch['total']} job{'s' if batch['total'] != 1 else ''}.\n\nOpen Career to watch progress and review {MIN_MATCH_SCORE}+ matches."
            return self._tool_response(answer, action, TASK_CAREER, decision, {"batch": batch})

        if action == "career_list_jobs":
            jobs = career_jobs.list_jobs(user_id=user_id)
            return self._tool_response(self._format_career_jobs(jobs), action, TASK_CAREER, decision, {"jobs": jobs})

        if action == "settings_show":
            payload = {
                "task_models": model_settings.get(user_id=user_id),
                "available_models": model_settings.available_models() or (ollama.list_models() if ollama.health() else []),
            }
            return self._tool_response(self._format_models(payload), action, "settings", decision, payload)

        if action == "analytics_summary":
            summary = analytics.summary(user_id=user_id, since_hours=24)
            return self._tool_response(self._format_analytics(summary), action, "analytics", decision, summary)

        if action == "open_page":
            page = str(args.get("page") or "").strip().lower()
            return self._tool_response(self._format_open_page(page), action, self._page_route(page), decision, {"page": page})

        result = general_agent.ask(query, session_id=session_id, model=model_settings.model_for(TASK_GENERAL, user_id=user_id), user_id=user_id)
        return self._with_workspace_meta(result, decision, TASK_GENERAL)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        return json.loads(match.group(0) if match else clean)

    @staticmethod
    def _with_workspace_meta(result: dict[str, Any], decision: dict[str, Any], route: str) -> dict[str, Any]:
        result["route"] = route
        result["workspace_action"] = decision["action"]
        result["workspace_router"] = {
            "source": decision.get("router_source"),
            "model": decision.get("router_model"),
            "confidence": decision.get("confidence"),
        }
        return result

    @staticmethod
    def _tool_response(answer: str, action: str, route: str, decision: dict[str, Any], data: Any = None) -> dict[str, Any]:
        return {
            "answer": answer,
            "sources": [],
            "chart": None,
            "model": decision.get("router_model"),
            "route": route,
            "workspace_action": action,
            "workspace_router": {
                "source": decision.get("router_source"),
                "model": decision.get("router_model"),
                "confidence": decision.get("confidence"),
            },
            "data": data,
        }

    @staticmethod
    def _priority_route(query: str) -> dict[str, Any] | None:
        text = query.lower()
        url = WorkspaceRouter._extract_url(query)
        if url and any(term in text for term in ["ingest", "upload", "save", "add", "read"]):
            return {"action": "docs_ingest_url", "confidence": 0.92, "arguments": {"url": url}}
        if re.search(r"\bremember that\b|\bremember this\b|\bsave this fact\b", text):
            return {"action": "memory_add_fact", "confidence": 0.92, "arguments": {"note": WorkspaceRouter._memory_fact_from_query(query)}}
        if any(term in text for term in ["save note", "add note", "new note", "ingest note"]):
            return {"action": "note_ingest", "confidence": 0.9, "arguments": {"note": WorkspaceRouter._note_from_query(query)}}
        page = WorkspaceRouter._page_from_query(text)
        if page and any(term in text for term in ["open", "go to", "take me to", "show me the page", "navigate"]):
            return {"action": "open_page", "confidence": 0.9, "arguments": {"page": page}}
        if any(term in text for term in ["score all", "score jobs", "score found"]):
            return {"action": "career_score_all", "confidence": 0.92, "arguments": {}}
        if (
            any(term in text for term in ["find job", "find jobs", "search job", "search jobs", "job search"])
            or re.search(r"\b(find|search|look for|scan for)\b.{0,80}\bjobs?\b", text)
            or re.search(r"\bjobs?\b.{0,80}\b(find|search)\b", text)
        ):
            return {"action": "career_search", "confidence": 0.92, "arguments": {}}
        return None

    @staticmethod
    def _fallback_route(query: str) -> dict[str, Any]:
        text = query.lower()
        url = WorkspaceRouter._extract_url(query)
        if url and any(term in text for term in ["ingest", "upload", "save", "add", "read"]):
            return {"action": "docs_ingest_url", "confidence": 0.82, "arguments": {"url": url}}
        if re.search(r"\bremember that\b|\bremember this\b|\bsave this fact\b", text):
            return {"action": "memory_add_fact", "confidence": 0.84, "arguments": {"note": WorkspaceRouter._memory_fact_from_query(query)}}
        if any(term in text for term in ["save note", "add note", "new note", "ingest note"]):
            return {"action": "note_ingest", "confidence": 0.8, "arguments": {"note": WorkspaceRouter._note_from_query(query)}}
        page = WorkspaceRouter._page_from_query(text)
        if page and any(term in text for term in ["open", "go to", "take me to", "show me the page", "navigate"]):
            return {"action": "open_page", "confidence": 0.84, "arguments": {"page": page}}
        if any(term in text for term in ["score all", "score jobs", "score found"]):
            return {"action": "career_score_all", "confidence": 0.86, "arguments": {}}
        if (
            any(term in text for term in ["find job", "find jobs", "search job", "search jobs", "job search"])
            or re.search(r"\b(find|search|look for|scan for)\b.{0,80}\bjobs?\b", text)
            or re.search(r"\bjobs?\b.{0,80}\b(find|search)\b", text)
        ):
            return {"action": "career_search", "confidence": 0.86, "arguments": {}}
        if any(term in text for term in ["career", "cv", "resume", "applied jobs", "saved jobs", "matched jobs", "found jobs"]):
            return {"action": "career_list_jobs", "confidence": 0.74, "arguments": {}}
        if any(term in text for term in ["documents", "docs", "uploaded files", "document library", "brain files"]):
            return {"action": "docs_list", "confidence": 0.82, "arguments": {}}
        if any(term in text for term in ["memory", "saved facts", "what did i", "remembered"]):
            return {"action": "memory_list", "confidence": 0.82, "arguments": {}}
        if any(term in text for term in ["models", "model", "settings", "provider"]):
            return {"action": "settings_show", "confidence": 0.78, "arguments": {}}
        if any(term in text for term in ["analytics", "usage", "latency", "queries"]):
            return {"action": "analytics_summary", "confidence": 0.78, "arguments": {}}
        if any(term in text for term in ["dataset", "csv", "excel", "chart", "sql", "kpi", "revenue"]):
            return {"action": "bi_ask", "confidence": 0.72, "arguments": {}}
        if any(term in text for term in ["document", "pdf", "according to", "summarize my", "uploaded"]):
            return {"action": "rag_ask", "confidence": 0.72, "arguments": {}}
        return {"action": "general_chat", "confidence": 0.55, "arguments": {}}

    @staticmethod
    def _extract_url(text: str) -> str:
        match = re.search(r"https?://[^\s)>\]]+", text)
        return match.group(0).rstrip(".,") if match else ""

    @staticmethod
    def _memory_fact_from_query(query: str) -> str:
        return re.sub(r"^\s*(please\s+)?(remember that|remember this|save this fact)[:\s]*", "", query, flags=re.IGNORECASE).strip()

    @staticmethod
    def _note_from_query(query: str) -> str:
        return re.sub(r"^\s*(please\s+)?(save|add|ingest)\s+(this\s+)?note[:\s]*", "", query, flags=re.IGNORECASE).strip()

    @staticmethod
    def _career_search_preferences_from_query(query: str) -> dict[str, str]:
        text = re.sub(r"\s+", " ", query).strip()
        lower = text.lower()
        preferences: dict[str, str] = {}

        role_match = re.search(
            r"\b(?:find|search|look for|scan for)\b\s+(?:me\s+)?(.{2,90}?)\s+jobs?\b",
            text,
            flags=re.IGNORECASE,
        )
        if role_match:
            role = role_match.group(1)
            role = re.sub(r"\b(remote|hybrid|onsite|on-site)\b", "", role, flags=re.IGNORECASE)
            role = re.sub(r"\b(part[- ]time|full[- ]time|contract|permanent|internship|intern)\b", r"\1", role, flags=re.IGNORECASE)
            role = re.sub(r"\b(using|with|from|for)\b.*$", "", role, flags=re.IGNORECASE)
            role = re.sub(r"\s+", " ", role).strip(" ,.-")
            if role:
                preferences["roles"] = role
                preferences["match_mode"] = "both"

        location_match = re.search(
            r"\b(?:in|near|around)\s+([A-Za-z][A-Za-z\s,.-]{1,60})(?:\s+(?:using|with|for|and)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
        if location_match:
            location = re.sub(r"\s+", " ", location_match.group(1)).strip(" ,.-")
            if location and location.lower() not in {"remote", "hybrid", "onsite", "on-site"}:
                preferences["locations"] = location

        if "hybrid" in lower:
            preferences["remote"] = "hybrid"
        elif re.search(r"\b(on[- ]?site|office[- ]based)\b", lower):
            preferences["remote"] = "onsite"
        elif re.search(r"\b(remote|work from home|wfh)\b", lower):
            preferences["remote"] = "remote"

        return preferences

    @staticmethod
    def _match_document_source(query: str, docs: list[dict[str, Any]]) -> str:
        if not docs:
            return ""
        clean_query = query.lower()
        for doc in docs:
            source = str(doc.get("source") or "")
            title = str(doc.get("title") or source)
            if source.lower() in clean_query or title.lower() in clean_query:
                return source
        return docs[0].get("source", "") if any(term in clean_query for term in ["first", "latest", "preview"]) else ""

    @staticmethod
    def _format_documents(docs: list[dict[str, Any]]) -> str:
        if not docs:
            return "Your document library is empty. Open Brain to upload a PDF, ingest a URL, or save a note."
        lines = [f"You have {len(docs)} document{'s' if len(docs) != 1 else ''} in Brain:", ""]
        for index, doc in enumerate(docs[:8], start=1):
            lines.append(f"{index}. {doc.get('title') or doc.get('source')} - {doc.get('chunks', 0)} chunks")
        if len(docs) > 8:
            lines.append(f"...and {len(docs) - 8} more.")
        return "\n".join(lines)

    @staticmethod
    def _format_document_preview(preview: dict[str, Any]) -> str:
        text = str(preview.get("text") or "").strip()
        if len(text) > 1800:
            text = text[:1800].rsplit(" ", 1)[0] + "..."
        return f"{preview.get('title') or preview.get('source')}\n\n{text or 'No preview text available.'}"

    @staticmethod
    def _format_memory(facts: list[dict[str, Any]], sessions: list[dict[str, Any]]) -> str:
        lines = [f"Saved facts: {len(facts)}", f"Memory sessions: {len(sessions)}", ""]
        if facts:
            lines.append("Recent facts:")
            for index, fact in enumerate(facts[-6:], start=1):
                lines.append(f"{index}. {fact.get('content')}")
        else:
            lines.append("No saved facts yet.")
        return "\n".join(lines)

    @staticmethod
    def _format_career_search(result: dict[str, Any]) -> str:
        saved = result.get("saved") if isinstance(result.get("saved"), list) else []
        sources = ", ".join(result.get("searched_sources") or []) or "none"
        criteria = result.get("workspace_criteria") if isinstance(result.get("workspace_criteria"), dict) else {}
        lines = [
            f"Found {result.get('count', len(saved))} new job{'s' if result.get('count', len(saved)) != 1 else ''}.",
            f"Sources searched: {sources}.",
            "",
        ]
        if criteria:
            criteria_bits = []
            if criteria.get("roles"):
                criteria_bits.append(f"role: {criteria['roles']}")
            if criteria.get("locations"):
                criteria_bits.append(f"location: {criteria['locations']}")
            if criteria.get("remote"):
                criteria_bits.append(f"work mode: {criteria['remote']}")
            if criteria_bits:
                lines.insert(2, f"Workspace criteria: {', '.join(criteria_bits)}.")
        if saved:
            lines.append("Newest results:")
            for index, job in enumerate(saved[:5], start=1):
                company = f" at {job.get('company')}" if job.get("company") else ""
                location = f" - {job.get('location')}" if job.get("location") else ""
                lines.append(f"{index}. {job.get('title', 'Untitled role')}{company}{location}")
            lines.append("")
            if result.get("will_score"):
                lines.append("Scoring has not started. Use Score All in Career when you want to score the found jobs.")
            lines.append("Opening Career so you can review the found jobs.")
        else:
            lines.append("No new jobs were saved. Try broadening your criteria in Career.")
        return "\n".join(lines)

    @staticmethod
    def _format_career_jobs(jobs: list[dict[str, Any]]) -> str:
        if not jobs:
            return "No career jobs are saved yet. Ask me to search jobs, or open Career to paste a job description."
        groups: dict[str, list[dict[str, Any]]] = {}
        for job in jobs:
            status = "matches" if isinstance(job.get("fit_score"), (int, float)) and job["fit_score"] >= MIN_MATCH_SCORE else job.get("status", "found")
            groups.setdefault(status, []).append(job)
        lines = ["Career jobs:"]
        for status in ["matches", "found", "saved", "applied", "skipped", "scored"]:
            if groups.get(status):
                lines.append(f"- {status}: {len(groups[status])}")
        lines.append("")
        for index, job in enumerate(jobs[:6], start=1):
            score = f" ({job['fit_score']}/100)" if isinstance(job.get("fit_score"), (int, float)) else ""
            lines.append(f"{index}. {job.get('title', 'Untitled role')} - {job.get('company', 'Unknown company')}{score}")
        return "\n".join(lines)

    @staticmethod
    def _format_models(payload: dict[str, Any]) -> str:
        task_models = payload.get("task_models") or {}
        lines = ["Current task model routing:", ""]
        lines.extend(f"- {task}: {model}" for task, model in task_models.items())
        return "\n".join(lines)

    @staticmethod
    def _format_analytics(summary: dict[str, Any]) -> str:
        return "\n".join([
            "Analytics summary for the last 24 hours:",
            "",
            f"- Total queries: {summary.get('total_queries', 0)}",
            f"- Success rate: {summary.get('success_rate', 'n/a')}",
            f"- Average latency: {summary.get('avg_latency_ms', 'n/a')} ms",
            f"- p95 latency: {summary.get('p95_latency_ms', 'n/a')} ms",
        ])

    @staticmethod
    def _format_open_page(page: str) -> str:
        labels = {
            "brain": "Brain",
            "documents": "Documents",
            "career": "Career",
            "dashboard": "BI Dashboard",
            "memory": "Memory",
            "analytics": "Analytics",
            "settings": "Settings",
        }
        return f"Open {labels.get(page, 'the relevant page')} from the action below."

    @staticmethod
    def _page_route(page: str) -> str:
        return {
            "brain": TASK_RAG,
            "documents": "documents",
            "career": TASK_CAREER,
            "dashboard": TASK_BI,
            "memory": TASK_MEMORY,
            "analytics": "analytics",
            "settings": "settings",
        }.get(page, TASK_GENERAL)

    @staticmethod
    def _page_from_query(text: str) -> str:
        page_aliases = {
            "brain": "brain",
            "documents": "documents",
            "docs": "documents",
            "career": "career",
            "jobs": "career",
            "dashboard": "dashboard",
            "bi": "dashboard",
            "memory": "memory",
            "analytics": "analytics",
            "settings": "settings",
        }
        for alias, page in page_aliases.items():
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return page
        return ""


workspace_router = WorkspaceRouter()
