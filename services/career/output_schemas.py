"""Provider-independent JSON shapes for Career's validated responses."""


def _object(properties: dict) -> dict:
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def _list(description: str) -> dict:
    return {"type": "array", "items": {"type": "string"}, "description": description}


FIT_ANALYSIS = _object({
    "fit_score": {"type": "number", "description": "Numeric fit score from 0 to 100."},
    "summary": {"type": "string", "description": "One concise paragraph."},
    "matched_skills": _list("Up to six skills supported by the CV."),
    "missing_or_weak_signals": _list("Up to five gaps."),
    "risks": _list("Up to four risks."),
    "recommended_cv_emphasis": _list("Supported experience to emphasize."),
    "application_decision": {"type": "string", "enum": ["apply", "maybe", "skip"]},
    "truth_check": _list("Claims supported by the CV."),
})

TAILORED_CV = _object({
    "headline": {"type": "string"},
    "professional_summary": {"type": "string", "description": "Two concise sentences."},
    "priority_keywords": _list("Up to six supported job keywords."),
    "tailored_bullets": _list("Up to four concise bullets supported by the CV."),
    "suggested_section_order": _list("Suggested CV section order."),
    "changes_made": _list("Brief descriptions of the changes."),
    "do_not_claim": _list("Unsupported claims to avoid."),
})

COVER_LETTER = _object({
    "cover_letter": {"type": "string", "description": "A complete cover letter of 90 to 120 words, never more than 300 words."},
    "customization_notes": _list("Up to two customization notes."),
    "questions_for_user": _list("Up to two missing details to ask about."),
})

_PACK_ANALYSIS = _object({
    key: value for key, value in FIT_ANALYSIS["properties"].items()
    if key not in {"recommended_cv_emphasis", "truth_check"}
})
_PACK_CV = _object({
    key: value for key, value in TAILORED_CV["properties"].items()
    if key not in {"suggested_section_order", "changes_made"}
})
APPLICATION_PACK = _object({
    "analysis": _PACK_ANALYSIS, "tailored_cv": _PACK_CV, "cover_letter": COVER_LETTER,
})
MATCH_PACK = _object({"tailored_cv": _PACK_CV, "cover_letter": COVER_LETTER})
