"""LangGraph Stage 2 Triage Preparation & Cited AI Medical Summarizer."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.services.agent_prompts import STAGE2_TRIAGE_REPAIR_PROMPT, STAGE2_TRIAGE_SUMMARIZER_PROMPT
from app.services.clinical_nlp import extract_facts_for_document, is_clinical_document, search_relevant_evidence
from app.services.intake_processing import (
    DocumentProcessingError,
    azure_review_completion,
    get_last_completion_audit,
    get_last_completion_provider,
)
from app.services.va_rules import (
    RULES_VERSION,
    analyze_evidence_gaps_and_pathway,
    detect_claim_programs,
    resolve_exam_suggestion,
)

logger = logging.getLogger(__name__)
STAGE2_PROMPT_VERSION = f"stage2-clinical-triage-v1.6-{hashlib.sha256(STAGE2_TRIAGE_SUMMARIZER_PROMPT.encode()).hexdigest()[:8]}"
NLP_CACHE_VERSION = "nlp-v4"
MAX_BATCH_INPUT_CHARS = 60000


class ContentionNarrative(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contentionId: str
    clinicalSummary: str
    citationLabels: list[str]


class CaseMedicalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caseOverview: str
    citationLabels: list[str]
    contentions: list[ContentionNarrative]


class TriageGraphState(TypedDict, total=False):
    esr_id: str
    esr: dict
    veteran: dict
    documents: list[dict]
    contentions: list[dict]
    allow_llm: bool
    existing_facts: list[dict]
    document_fact_cache: dict[str, dict]
    # Computed outputs
    extracted_facts: list[dict]
    distress_alerts: list[dict]
    updated_fact_cache: dict[str, dict]
    program_flags: dict
    contention_packets: dict[str, dict]
    relationship_map: list[dict]
    evidence_coverage: dict[str, Any]
    case_overview: str
    case_citations: list[str]
    ai_provider: str
    ai_unavailable: bool
    processing_audit: dict[str, Any]


def compute_triage_digest(esr: dict, documents: list[dict], contentions: list[dict]) -> str:
    """Deterministic idempotency digest for Stage 2 preparation (reuses cached plan when inputs haven't changed)."""
    active_docs = sorted(
        f"{d['id']}:{d.get('sourceHash', '')}:{d.get('classificationStatus', '')}:{d.get('version', 1)}"
        for d in documents
    )
    active_contentions = sorted(
        f"{c['id']}:{c.get('title', '')}:{','.join(c.get('requestedDbqNames', []))}"
        for c in contentions
    )
    raw = "|".join([esr.get("id", ""), *active_docs, *active_contentions, RULES_VERSION, STAGE2_PROMPT_VERSION])
    return hashlib.sha256(raw.encode()).hexdigest()


def node_extract_clinical_nlp(graph_state: TriageGraphState) -> dict:
    esr_id = graph_state["esr_id"]
    documents = graph_state["documents"]
    prev_cache = graph_state.get("document_fact_cache") or {}
    next_cache: dict[str, dict] = {}
    all_facts: list[dict] = []
    all_distress: list[dict] = []

    for doc in documents:
        if not is_clinical_document(doc):
            continue
        doc_id = doc["id"]
        doc_key = f"{doc.get('sourceHash', '')}:{doc.get('version', 1)}:{doc.get('classifiedPageCount', 0)}:{NLP_CACHE_VERSION}"
        cached = prev_cache.get(doc_id)
        if cached and cached.get("docKey") == doc_key:
            extracted = {"facts": cached.get("facts", []), "distressAlerts": cached.get("distressAlerts", [])}
        else:
            extracted = extract_facts_for_document(esr_id, doc)
        next_cache[doc_id] = {
            "docKey": doc_key,
            "facts": extracted["facts"],
            "distressAlerts": extracted["distressAlerts"],
        }
        all_facts.extend(extracted["facts"])
        all_distress.extend(extracted["distressAlerts"])

    return {"extracted_facts": all_facts, "distress_alerts": all_distress, "updated_fact_cache": next_cache}


def node_match_contention_evidence(graph_state: TriageGraphState) -> dict:
    esr_id = graph_state["esr_id"]
    documents = graph_state["documents"]
    facts = graph_state.get("extracted_facts") or []
    contentions = graph_state["contentions"]

    packets: dict[str, dict] = {}
    for contention in contentions:
        retrieval = search_relevant_evidence(esr_id, contention, documents, facts)
        packets[contention["id"]] = {
            "contentionId": contention["id"],
            "title": contention.get("title", ""),
            "retrieval": retrieval,
        }
    return {"contention_packets": packets}


def node_evaluate_va_rules_and_gaps(graph_state: TriageGraphState) -> dict:
    esr = graph_state["esr"]
    veteran = graph_state["veteran"]
    documents = graph_state["documents"]
    contentions = graph_state["contentions"]
    packets = dict(graph_state.get("contention_packets") or {})

    program_flags = detect_claim_programs(esr, veteran, contentions)

    for contention in contentions:
        cid = contention["id"]
        packet = dict(packets.get(cid) or {})
        retrieval = packet.get("retrieval") or {}
        matched_pages = retrieval.get("matchedPages") or []
        matched_facts = retrieval.get("matchedFacts") or []

        exam_info = resolve_exam_suggestion(contention, matched_facts)
        rules_eval = analyze_evidence_gaps_and_pathway(
            contention, exam_info, matched_pages, matched_facts, documents, program_flags, esr=esr
        )
        packet["examSuggestion"] = exam_info
        packet["rulesEval"] = rules_eval
        packets[cid] = packet

    return {"program_flags": program_flags, "contention_packets": packets}


def _deterministic_chronological_summary(
    claimant_name: str,
    contentions: list[dict],
    packets: dict[str, dict],
    facts: list[dict],
) -> tuple[str, list[str], dict[str, tuple[str, list[str]]]]:
    """Build clean chronological cited summary directly from verified Stage 2 NLP facts and matched pages."""
    case_cites: list[str] = []
    timeline_bits: list[str] = []

    sorted_facts = sorted(
        (f for f in facts if not f.get("isFamilyHistory")),
        key=lambda f: (not f.get("clinicalDate"), f.get("clinicalDate", ""), f.get("pageNumber", 1)),
    )
    dated_values = sorted({f["clinicalDate"] for f in sorted_facts if f.get("clinicalDate")})
    date_span = (
        f" (records spanning {dated_values[0]} to {dated_values[-1]})"
        if len(dated_values) >= 2
        else f" (dated {dated_values[0]})" if len(dated_values) == 1 else ""
    )
    active_bits: list[str] = []
    hist_bits: list[str] = []
    neg_bits: list[str] = []
    seen_snippets: set[str] = set()
    for fact in sorted_facts:
        snippet = fact.get("rawExcerpt", "")[:140].rstrip(".")
        norm_snippet = snippet.casefold()
        if not snippet or norm_snippet in seen_snippets or len(snippet.split()) <= 3:
            continue
        seen_snippets.add(norm_snippet)
        cite = f"{fact['documentTitle']} · p. {fact['pageNumber']}"
        if cite not in case_cites and len(case_cites) < 10:
            case_cites.append(cite)
        date_prefix = f"{fact['clinicalDate']}: " if fact.get("clinicalDate") else ""
        if fact.get("isNegated") and len(neg_bits) < 2:
            neg_bits.append(f"{date_prefix}{snippet}.")
        elif (fact.get("isHistorical") or fact.get("isResolved")) and len(hist_bits) < 2:
            hist_bits.append(f"{date_prefix}{snippet}.")
        elif not fact.get("isNegated") and len(active_bits) < 4:
            active_bits.append(f"{date_prefix}{snippet}.")

    all_treatments = list(dict.fromkeys(t for f in sorted_facts for t in (f.get("treatments") or [])))
    all_limits = list(dict.fromkeys(l for f in sorted_facts for l in (f.get("functionalLimitations") or [])))

    summary_parts: list[str] = [
        f"Claimant {claimant_name or 'record'} has {len(contentions)} requested examination(s){date_span}."
    ]
    if active_bits:
        summary_parts.append("Clinical findings: " + " ".join(active_bits[:3]))
    if hist_bits:
        summary_parts.append("Historical/resolved: " + " ".join(hist_bits[:2]))
    if all_treatments:
        summary_parts.append(f"Treatments: {', '.join(all_treatments[:6])}.")
    if all_limits:
        summary_parts.append(f"Functional impact: {', '.join(all_limits[:4])}.")
    if neg_bits:
        summary_parts.append("Pertinent negatives: " + " ".join(neg_bits[:1]))

    if len(summary_parts) > 1:
        case_overview = " ".join(summary_parts)
    else:
        case_overview = (
            f"Claimant {claimant_name or 'record'} has {len(contentions)} requested examination(s) awaiting clinical review. "
            "Review linked medical records for detailed clinical history."
        )

    per_contention: dict[str, tuple[str, list[str]]] = {}
    for contention in contentions:
        cid = contention["id"]
        packet = packets.get(cid) or {}
        retrieval = packet.get("retrieval") or {}
        matched_pages = retrieval.get("matchedPages") or []
        matched_facts = retrieval.get("matchedFacts") or []

        c_cites: list[str] = []
        for mp in matched_pages:
            label = f"{mp['documentTitle']} · p. {mp['page']}"
            if label not in c_cites:
                c_cites.append(label)
                if label not in case_cites and len(case_cites) < 10:
                    case_cites.append(label)

        lines: list[str] = []
        active = [f for f in matched_facts if f.get("assertion") == "present"]
        historical = [f for f in matched_facts if f.get("assertion") in {"historical", "resolved"}]
        suspected = [f for f in matched_facts if f.get("assertion") == "suspected"]
        negated = [f for f in matched_facts if f.get("assertion") == "negated"]

        for f in active[:3]:
            dt = f"({f['clinicalDate']}) " if f.get("clinicalDate") else ""
            lines.append(f"{dt}{f['rawExcerpt'][:160].rstrip('.')}.")
        if historical:
            h0 = historical[0]
            dt = f"({h0['clinicalDate']}) " if h0.get("clinicalDate") else ""
            lines.append(f"Historical/Resolved: {dt}{h0['rawExcerpt'][:130].rstrip('.')}.")
        if suspected:
            s0 = suspected[0]
            lines.append(f"Suspected: {s0['rawExcerpt'][:130].rstrip('.')}.")
        if negated:
            n0 = negated[0]
            lines.append(f"Negated/Denied: {n0['rawExcerpt'][:130].rstrip('.')}.")
        treatments = list(dict.fromkeys(t for f in matched_facts for t in (f.get("treatments") or [])))
        if treatments:
            lines.append(f"Documented treatments: {', '.join(treatments[:5])}.")
        limits = list(dict.fromkeys(l for f in matched_facts for l in (f.get("functionalLimitations") or [])))
        if limits:
            lines.append(f"Functional limitations: {', '.join(limits[:4])}.")
        if not lines and matched_pages:
            lines.append(f"Relevant evidence identified across {len(matched_pages)} page(s); expand evidence excerpts to inspect full records.")
        if not lines:
            lines.append("No condition-specific clinical narrative matched in extracted text; inspect source documents directly.")
        per_contention[cid] = (" ".join(lines), c_cites[:8])

    return case_overview, case_cites[:10], per_contention


def _build_relationship_map_and_coverage(
    documents: list[dict],
    contentions: list[dict],
    packets: dict[str, dict],
    facts: list[dict],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Map clinical relationships across contentions and flag any uncovered medical evidence."""
    matched_fact_ids: set[str] = set()
    matched_doc_ids: set[str] = set()
    doc_to_contentions: dict[str, list[str]] = {}
    treatment_links: dict[str, set[str]] = {}

    for contention in contentions:
        cid = contention["id"]
        retrieval = (packets.get(cid) or {}).get("retrieval") or {}
        for mp in retrieval.get("matchedPages") or []:
            did = mp.get("documentId")
            if did:
                matched_doc_ids.add(did)
                doc_to_contentions.setdefault(did, [])
                if cid not in doc_to_contentions[did]:
                    doc_to_contentions[did].append(cid)
        for fact in retrieval.get("matchedFacts") or []:
            if fact.get("id"):
                matched_fact_ids.add(fact["id"])
            for tr in fact.get("treatments") or []:
                treatment_links.setdefault(tr, set()).add(cid)

    relationships: list[dict[str, Any]] = []
    for doc_id, cids in doc_to_contentions.items():
        if len(cids) > 1:
            relationships.append({
                "kind": "shared-evidence-document",
                "referenceId": doc_id,
                "contentionIds": cids,
            })
    for tr, cids in treatment_links.items():
        if len(cids) > 1:
            relationships.append({
                "kind": "shared-treatment",
                "label": tr,
                "contentionIds": sorted(cids),
            })

    clinical_docs = [d for d in documents if is_clinical_document(d)]
    unmapped_docs = [
        {"documentId": d["id"], "title": d.get("title") or d.get("sourceReference", "")}
        for d in clinical_docs
        if d["id"] not in matched_doc_ids
    ]
    unmapped_facts = [
        {
            "factId": f["id"],
            "documentTitle": f.get("documentTitle", ""),
            "pageNumber": f.get("pageNumber", 1),
            "assertion": f.get("assertion", "present"),
        }
        for f in facts
        if f.get("id") not in matched_fact_ids and not f.get("isFamilyHistory")
    ]
    coverage = {
        "clinicalDocumentCount": len(clinical_docs),
        "matchedDocumentCount": len(matched_doc_ids),
        "totalFactCount": len(facts),
        "matchedFactCount": len(matched_fact_ids),
        "unmappedDocuments": unmapped_docs[:25],
        "unmappedFactCount": len(unmapped_facts),
        "complete": len(unmapped_docs) == 0,
    }
    return relationships, coverage


def _safe_validation_diagnostics(exc: ValidationError) -> list[dict[str, str]]:
    """Extract non-PHI field path, error type, and constraint metadata from Pydantic ValidationError."""
    diagnostics: list[dict[str, str]] = []
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        ctx = err.get("ctx") or {}
        constraint = ", ".join(f"{k}={v}" for k, v in ctx.items()) if ctx else ""
        diagnostics.append({
            "field": loc or "<root>",
            "type": str(err.get("type", "validation_error")),
            "constraint": constraint,
        })
    return diagnostics


def _coerce_json_list(value: Any) -> list[Any]:
    """Coerce stringified JSON lists, single dicts, or keyed maps into a Python list."""
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
            value = decoded
        except ValueError:
            return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if "contentionId" in value or "clinicalSummary" in value or "summary" in value:
            return [value]
        out: list[Any] = []
        for k, v in value.items():
            if isinstance(v, dict):
                item = dict(v)
                item.setdefault("contentionId", str(k))
                out.append(item)
        return out
    return []


def _normalize_summary_dict(raw_payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize provider-specific wrapper quirks (e.g. stringified arrays) before strict Pydantic validation."""
    if not isinstance(raw_payload, dict):
        return {}
    normalized = dict(raw_payload)
    if "caseOverview" not in normalized:
        for alt in ("case_overview", "overview"):
            if alt in normalized:
                normalized["caseOverview"] = normalized.pop(alt)
                break
    if "citationLabels" not in normalized and "citations" in normalized:
        normalized["citationLabels"] = normalized.pop("citations")
    if "contentions" not in normalized:
        for alt in ("contentionNarratives", "contention_summaries"):
            if alt in normalized:
                normalized["contentions"] = normalized.pop(alt)
                break

    if "citationLabels" in normalized:
        normalized["citationLabels"] = [str(c) for c in _coerce_json_list(normalized["citationLabels"])]
    if "contentions" in normalized:
        coerced_items = []
        for entry in _coerce_json_list(normalized["contentions"]):
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            if "contentionId" not in item and "id" in item:
                item["contentionId"] = item.pop("id")
            if "clinicalSummary" not in item and "summary" in item:
                item["clinicalSummary"] = item.pop("summary")
            if "citationLabels" not in item and "citations" in item:
                item["citationLabels"] = item.pop("citations")
            item["citationLabels"] = [str(c) for c in _coerce_json_list(item.get("citationLabels", []))]
            coerced_items.append(item)
        normalized["contentions"] = coerced_items
    return normalized


def _recover_partial_summary(
    raw_payload: dict[str, Any],
    expected_cids: set[str],
    valid_citations: set[str],
) -> tuple[str | None, list[str], dict[str, ContentionNarrative], list[str]]:
    """Recover valid root and per-contention fields independently without discarding whole responses."""
    recovered_fields: list[str] = []
    normalized = _normalize_summary_dict(raw_payload)

    overview_val = normalized.get("caseOverview") or ""
    case_overview = str(overview_val).strip() if isinstance(overview_val, str) and overview_val.strip() else None
    if case_overview:
        recovered_fields.append("caseOverview")

    raw_case_cites = normalized.get("citationLabels") or []
    case_cites = [
        str(c) for c in (raw_case_cites if isinstance(raw_case_cites, list) else [])
        if str(c) in valid_citations
    ]

    raw_contentions = normalized.get("contentions") or []
    recovered_contentions: dict[str, ContentionNarrative] = {}
    if isinstance(raw_contentions, list):
        for entry in raw_contentions:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("contentionId") or "").strip()
            summary = str(entry.get("clinicalSummary") or "").strip()
            cites_raw = entry.get("citationLabels") or []
            cites = [
                str(c) for c in (cites_raw if isinstance(cites_raw, list) else [])
                if str(c) in valid_citations
            ]
            if cid and summary and (not expected_cids or cid in expected_cids):
                recovered_contentions[cid] = ContentionNarrative(
                    contentionId=cid,
                    clinicalSummary=summary,
                    citationLabels=cites,
                )
                recovered_fields.append(f"contention:{cid}")
    return case_overview, case_cites, recovered_contentions, recovered_fields


def _build_llm_batches(
    contentions: list[dict],
    packets: dict[str, dict],
    relationship_map: list[dict[str, Any]],
) -> tuple[list[list[dict[str, Any]]], set[str]]:
    """Build complete, non-truncated evidence batches across all matched pages and facts."""
    valid_citations: set[str] = set()
    contention_entries: list[dict[str, Any]] = []

    for contention in contentions:
        cid = contention["id"]
        packet = packets.get(cid) or {}
        retrieval = packet.get("retrieval") or {}
        matched_pages = retrieval.get("matchedPages") or []
        matched_facts = retrieval.get("matchedFacts") or []

        page_snippets: list[dict[str, Any]] = []
        for mp in matched_pages:
            label = f"{mp['documentTitle']} · p. {mp['page']}"
            valid_citations.add(label)
            page_snippets.append({
                "citation": label,
                "date": mp.get("clinicalDate", ""),
                "excerpt": mp.get("fullPageText") or mp.get("excerpt", ""),
            })

        fact_entries_by_cite: dict[str, list[dict[str, Any]]] = {}
        all_fact_entries: list[dict[str, Any]] = []
        seen_fact_keys: set[tuple[str, str, str]] = set()
        for f in matched_facts:
            cite = f"{f.get('documentTitle')} · p. {f.get('pageNumber')}"
            if f.get("documentTitle") and f.get("pageNumber"):
                valid_citations.add(cite)
            fact_key = (cite, str(f.get("assertion", "")), str(f.get("rawExcerpt", ""))[:80])
            if fact_key in seen_fact_keys:
                continue
            seen_fact_keys.add(fact_key)
            fact_item = {
                "date": f.get("clinicalDate", ""),
                "assertion": f.get("assertion", ""),
                "laterality": f.get("laterality", ""),
                "measurements": f.get("measurements", {}),
                "codes": [c.get("code") for c in f.get("codes", [])],
                "citation": cite,
            }
            all_fact_entries.append(fact_item)
            fact_entries_by_cite.setdefault(cite, []).append(fact_item)

        if not page_snippets and not all_fact_entries:
            continue
        rels = [r for r in relationship_map if cid in (r.get("contentionIds") or [])]
        base_entry = {
            "contentionId": cid,
            "contentionTitle": contention.get("title", ""),
            "requestedDbqs": contention.get("requestedDbqNames") or [],
            "clinicalRelationships": rels,
            "structuredFacts": all_fact_entries,
            "excerpts": page_snippets,
        }
        serialized_len = len(json.dumps(base_entry, ensure_ascii=False))
        if serialized_len <= MAX_BATCH_INPUT_CHARS or len(page_snippets) <= 1:
            contention_entries.append(base_entry)
        else:
            chunk_pages: list[dict[str, Any]] = []
            for snippet in page_snippets:
                chunk_pages.append(snippet)
                chunk_cites = {p["citation"] for p in chunk_pages}
                chunk_facts = [item for c in chunk_cites for item in fact_entries_by_cite.get(c, [])]
                candidate = {**base_entry, "structuredFacts": chunk_facts, "excerpts": chunk_pages}
                if len(json.dumps(candidate, ensure_ascii=False)) >= MAX_BATCH_INPUT_CHARS and len(chunk_pages) > 1:
                    last = chunk_pages.pop()
                    prev_cites = {p["citation"] for p in chunk_pages}
                    prev_facts = [item for c in prev_cites for item in fact_entries_by_cite.get(c, [])]
                    contention_entries.append({**base_entry, "structuredFacts": prev_facts, "excerpts": chunk_pages})
                    chunk_pages = [last]
            if chunk_pages:
                tail_cites = {p["citation"] for p in chunk_pages}
                tail_facts = [item for c in tail_cites for item in fact_entries_by_cite.get(c, [])]
                contention_entries.append({**base_entry, "structuredFacts": tail_facts, "excerpts": chunk_pages})

    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_size = 0
    for entry in contention_entries:
        entry_size = len(json.dumps(entry, ensure_ascii=False))
        if current_batch and current_size + entry_size > MAX_BATCH_INPUT_CHARS:
            batches.append(current_batch)
            current_batch = [entry]
            current_size = entry_size
        else:
            current_batch.append(entry)
            current_size += entry_size
    if current_batch:
        batches.append(current_batch)
    return batches, valid_citations


def run_guarded_triage_llm(
    claimant_name: str,
    contentions: list[dict],
    packets: dict[str, dict],
    relationship_map: list[dict[str, Any]] | None = None,
) -> tuple[CaseMedicalSummary | None, str, bool, dict[str, Any]]:
    """Batched, schema-repaired LLM wrapper with independent per-contention validation and non-PHI telemetry."""
    audit: dict[str, Any] = {
        "provider": "local",
        "batches": 0,
        "repairAttempted": False,
        "validationDiagnostics": [],
        "recoveredSections": [],
    }
    if settings.intake_provider != "azure-openai":
        return None, "local", False, audit

    batches, valid_citations = _build_llm_batches(contentions, packets, relationship_map or [])
    if not valid_citations or not batches:
        return None, "local", False, audit

    audit["batches"] = len(batches)
    schema = CaseMedicalSummary.model_json_schema()
    instructions = STAGE2_TRIAGE_SUMMARIZER_PROMPT

    overview_parts: list[str] = []
    merged_case_cites: list[str] = []
    merged_contentions: dict[str, ContentionNarrative] = {}
    last_provider = "local"
    any_success = False

    for batch_idx, batch_payload in enumerate(batches, 1):
        batch_cids = {entry["contentionId"] for entry in batch_payload}
        max_tokens = min(3500, max(1200, 750 + 400 * len(batch_payload)))
        raw_json = ""
        try:
            raw_json = azure_review_completion(
                "triage_case_summary",
                schema,
                instructions,
                batch_payload,
                max_tokens=max_tokens,
            )
            last_provider = get_last_completion_provider()
            audit["router"] = get_last_completion_audit()
            raw_dict = json.loads(raw_json)
            parsed = CaseMedicalSummary.model_validate(_normalize_summary_dict(raw_dict))
            if parsed.caseOverview.strip():
                overview_parts.append(parsed.caseOverview.strip())
            for cite in parsed.citationLabels:
                if cite in valid_citations and cite not in merged_case_cites:
                    merged_case_cites.append(cite)
            for item in parsed.contentions:
                clean_cites = [c for c in item.citationLabels if c in valid_citations]
                if item.contentionId in merged_contentions and item.clinicalSummary.strip():
                    existing = merged_contentions[item.contentionId]
                    existing.clinicalSummary = f"{existing.clinicalSummary} {item.clinicalSummary.strip()}".strip()
                    existing.citationLabels = list(dict.fromkeys([*existing.citationLabels, *clean_cites]))
                elif item.clinicalSummary.strip():
                    merged_contentions[item.contentionId] = ContentionNarrative(
                        contentionId=item.contentionId,
                        clinicalSummary=item.clinicalSummary.strip(),
                        citationLabels=clean_cites,
                    )
            any_success = True
        except ValidationError as exc:
            diags = _safe_validation_diagnostics(exc)
            audit["validationDiagnostics"].extend(diags)
            logger.warning(
                "Stage 2 AI summarizer batch %s/%s validation diagnostics: %s",
                batch_idx,
                len(batches),
                diags,
            )
            recovered_overview: str | None = None
            recovered_cites: list[str] = []
            recovered_items: dict[str, ContentionNarrative] = {}
            try:
                raw_dict = json.loads(raw_json) if raw_json else {}
                recovered_overview, recovered_cites, recovered_items, sec_names = _recover_partial_summary(
                    raw_dict, batch_cids, valid_citations
                )
                audit["recoveredSections"].extend(sec_names)
            except ValueError:
                raw_dict = {}

            missing_cids = batch_cids - set(recovered_items.keys())
            if (not recovered_overview or missing_cids) and not audit["repairAttempted"] and raw_json:
                audit["repairAttempted"] = True
                repair_instructions = STAGE2_TRIAGE_REPAIR_PROMPT
                try:
                    repaired_raw = azure_review_completion(
                        "triage_case_summary",
                        schema,
                        repair_instructions,
                        [{"diagnostics": diags, "candidateJson": raw_dict or raw_json[:6000]}],
                        max_tokens=max_tokens,
                    )
                    repaired_dict = json.loads(repaired_raw)
                    rep_ov, rep_cites, rep_items, rep_secs = _recover_partial_summary(
                        repaired_dict, batch_cids, valid_citations
                    )
                    recovered_overview = recovered_overview or rep_ov
                    recovered_cites = list(dict.fromkeys([*recovered_cites, *rep_cites]))
                    for cid, item in rep_items.items():
                        recovered_items.setdefault(cid, item)
                    audit["recoveredSections"].extend(rep_secs)
                    last_provider = get_last_completion_provider()
                except Exception as repair_exc:
                    logger.warning("Stage 2 AI bounded schema repair failed (%s).", type(repair_exc).__name__)

            if recovered_overview:
                overview_parts.append(recovered_overview)
                any_success = True
            for cite in recovered_cites:
                if cite not in merged_case_cites:
                    merged_case_cites.append(cite)
            for cid, item in recovered_items.items():
                if cid in merged_contentions:
                    existing = merged_contentions[cid]
                    existing.clinicalSummary = f"{existing.clinicalSummary} {item.clinicalSummary}".strip()
                    existing.citationLabels = list(dict.fromkeys([*existing.citationLabels, *item.citationLabels]))
                else:
                    merged_contentions[cid] = item
                any_success = True
        except (DocumentProcessingError, ValueError, Exception) as exc:
            logger.warning(
                "Stage 2 AI summarizer batch %s/%s failed (%s); preserving completed batches.",
                batch_idx,
                len(batches),
                type(exc).__name__,
            )
            audit["router"] = get_last_completion_audit()

    if not any_success:
        return None, "local", True, audit

    audit["provider"] = last_provider
    combined = CaseMedicalSummary(
        caseOverview="\n\n".join(dict.fromkeys(overview_parts)).strip(),
        citationLabels=merged_case_cites,
        contentions=list(merged_contentions.values()),
    )
    return combined, last_provider, False, audit


def node_generate_ai_dossier(graph_state: TriageGraphState) -> dict:
    veteran = graph_state["veteran"]
    documents = graph_state.get("documents") or []
    contentions = graph_state["contentions"]
    packets = dict(graph_state.get("contention_packets") or {})
    facts = graph_state.get("extracted_facts") or []
    claimant_name = veteran.get("displayName") or ""

    relationship_map, evidence_coverage = _build_relationship_map_and_coverage(
        documents, contentions, packets, facts
    )
    det_overview, det_cites, det_per_contention = _deterministic_chronological_summary(
        claimant_name, contentions, packets, facts
    )

    ai_result = None
    ai_provider = "local"
    ai_unavailable = False
    processing_audit: dict[str, Any] = {"provider": "local", "batches": 0}
    if graph_state.get("allow_llm"):
        ai_result, ai_provider, ai_unavailable, processing_audit = run_guarded_triage_llm(
            claimant_name, contentions, packets, relationship_map
        )

    case_overview = det_overview
    case_cites = det_cites
    if ai_result and ai_result.caseOverview.strip():
        case_overview = ai_result.caseOverview.strip()
        if ai_result.citationLabels:
            case_cites = ai_result.citationLabels

    ai_map = {item.contentionId: item for item in (ai_result.contentions if ai_result else [])}
    for contention in contentions:
        cid = contention["id"]
        packet = dict(packets.get(cid) or {})
        det_text, det_c_cites = det_per_contention.get(cid, ("", []))
        ai_item = ai_map.get(cid)
        if ai_item and ai_item.clinicalSummary.strip():
            packet["aiSummary"] = ai_item.clinicalSummary.strip()
            packet["citations"] = ai_item.citationLabels or det_c_cites
            packet["summaryProvider"] = ai_provider
        else:
            packet["aiSummary"] = det_text
            packet["citations"] = det_c_cites
            packet["summaryProvider"] = "local"
        packets[cid] = packet

    return {
        "contention_packets": packets,
        "relationship_map": relationship_map,
        "evidence_coverage": evidence_coverage,
        "case_overview": case_overview,
        "case_citations": case_cites,
        "ai_provider": ai_provider,
        "ai_unavailable": ai_unavailable,
        "processing_audit": processing_audit,
    }


def _build_triage_graph():
    graph = StateGraph(TriageGraphState)
    graph.add_node("extract_clinical_nlp", node_extract_clinical_nlp)
    graph.add_node("match_contention_evidence", node_match_contention_evidence)
    graph.add_node("evaluate_va_rules", node_evaluate_va_rules_and_gaps)
    graph.add_node("generate_ai_dossier", node_generate_ai_dossier)

    graph.add_edge(START, "extract_clinical_nlp")
    graph.add_edge("extract_clinical_nlp", "match_contention_evidence")
    graph.add_edge("match_contention_evidence", "evaluate_va_rules")
    graph.add_edge("evaluate_va_rules", "generate_ai_dossier")
    graph.add_edge("generate_ai_dossier", END)
    return graph.compile()


triage_workflow_graph = _build_triage_graph()


def run_triage_pipeline_for_state(state: dict, esr_id: str, *, allow_llm: bool = False, force: bool = False) -> dict | None:
    """Execute the Stage 2 LangGraph dossier builder for an ESR and persist structured facts & dossier."""
    esr = state.get("esrsById", {}).get(esr_id)
    if not esr:
        return None
    documents = [
        state["evidenceDocumentsById"][doc_id]
        for doc_id in esr.get("evidenceDocumentIds", [])
        if doc_id in state.get("evidenceDocumentsById", {})
    ]
    contentions = [
        state["contentionsById"][cid]
        for cid in esr.get("contentionIds", [])
        if cid in state.get("contentionsById", {})
    ]
    digest = compute_triage_digest(esr, documents, contentions)

    existing_dossiers = state.setdefault("triageDossiersByEsrId", {})
    existing = existing_dossiers.get(esr_id)
    if not force and existing and existing.get("sourceDigest") == digest:
        if not allow_llm or existing.get("aiAttempted"):
            return existing

    veteran = state.get("veteransById", {}).get(esr.get("veteranId"), {})
    result = triage_workflow_graph.invoke({
        "esr_id": esr_id,
        "esr": esr,
        "veteran": veteran,
        "documents": documents,
        "contentions": contentions,
        "allow_llm": allow_llm,
        "document_fact_cache": (existing or {}).get("documentFactCache") or {},
    })

    # Update clinicalFactsById cleanly (replace old facts for this ESR so exclusions stay accurate)
    facts_store = state.setdefault("clinicalFactsById", {})
    for k in [key for key, row in facts_store.items() if row.get("esrId") == esr_id]:
        del facts_store[k]
    for fact in result.get("extracted_facts") or []:
        facts_store[fact["id"]] = fact

    # Build lightweight serializable packet per contention
    serialized_contentions: dict[str, Any] = {}
    for cid, packet in (result.get("contention_packets") or {}).items():
        retrieval = packet.get("retrieval") or {}
        matched_pages = retrieval.get("matchedPages") or []
        matched_facts = retrieval.get("matchedFacts") or []

        # Aggregate discrete clinical metrics for UI highlight boxes
        discrete_metrics: list[dict[str, str]] = []
        seen_metrics: set[str] = set()
        all_codes: list[dict[str, str]] = []
        seen_code_keys: set[str] = set()
        for f in matched_facts:
            for c_info in f.get("codes", []):
                ck = f"{c_info.get('system')}:{c_info.get('code')}"
                if ck not in seen_code_keys:
                    seen_code_keys.add(ck)
                    all_codes.append(c_info)
            if f.get("isNegated") or f.get("isFamilyHistory"):
                continue
            m = f.get("measurements") or {}
            lat_prefix = f"{f['laterality'].capitalize()} {f['bodyPart']}" if f.get("laterality") not in ("", "unspecified") else f.get("bodyPart", "").capitalize()
            for field, label in (("flexion", "flexion"), ("extension", "extension"), ("abduction", "abduction"), ("painfulMotion", "painful motion")):
                if field in m:
                    key = f"{lat_prefix}:{label}"
                    if key not in seen_metrics:
                        seen_metrics.add(key)
                        discrete_metrics.append({
                            "value": f"{m[field]}°",
                            "label": f"{lat_prefix} {label}".strip(),
                            "date": f.get("clinicalDate", ""),
                            "source": f"{f.get('documentTitle')} · p. {f.get('pageNumber')}",
                        })
            if "bsaPercent" in m and "bsa" not in seen_metrics:
                seen_metrics.add("bsa")
                discrete_metrics.append({"value": f"{m['bsaPercent']}% BSA", "label": "Affected skin area", "date": f.get("clinicalDate", ""), "source": f"{f.get('documentTitle')} · p. {f.get('pageNumber')}"})
            if "speechDiscriminationPercent" in m and "speech" not in seen_metrics:
                seen_metrics.add("speech")
                discrete_metrics.append({"value": f"{m['speechDiscriminationPercent']}%", "label": "Speech discrimination", "date": f.get("clinicalDate", ""), "source": f"{f.get('documentTitle')} · p. {f.get('pageNumber')}"})

        serialized_contentions[cid] = {
            "contentionId": cid,
            "title": packet.get("title", ""),
            "aiSummary": packet.get("aiSummary", ""),
            "citations": packet.get("citations", []),
            "summaryProvider": packet.get("summaryProvider", "local"),
            "searchTermsUsed": retrieval.get("searchTerms", [])[:12],
            "usedFallback": retrieval.get("usedFallback", False),
            "matchedEvidence": [
                {
                    "documentId": mp["documentId"],
                    "documentTitle": mp["documentTitle"],
                    "category": mp["category"],
                    "page": mp["page"],
                    "clinicalDate": mp.get("clinicalDate", ""),
                    "matchedTerms": mp.get("matchedTerms", []),
                    "excerpt": mp["excerpt"][:1200],
                }
                for mp in matched_pages
            ],
            "extractedMetrics": discrete_metrics[:6],
            "medicalCodes": all_codes[:10],
            "factsCount": len(matched_facts),
            "negatedCount": sum(1 for f in matched_facts if f.get("isNegated")),
            "examSuggestion": packet.get("examSuggestion") or {},
            "rulesEval": packet.get("rulesEval") or {},
        }

    dossier = {
        "esrId": esr_id,
        "sourceDigest": digest,
        "ruleVersion": RULES_VERSION,
        "promptVersion": STAGE2_PROMPT_VERSION,
        "aiProvider": result.get("ai_provider", "local"),
        "aiAttempted": allow_llm,
        "aiUnavailable": result.get("ai_unavailable", False),
        "caseOverview": result.get("case_overview", ""),
        "caseCitations": result.get("case_citations", []),
        "distressAlerts": result.get("distress_alerts", []),
        "programFlags": result.get("program_flags") or {},
        "relationshipMap": result.get("relationship_map") or [],
        "evidenceCoverage": result.get("evidence_coverage") or {},
        "processingAudit": result.get("processing_audit") or {},
        "contentionsById": serialized_contentions,
        "factCount": len(result.get("extracted_facts") or []),
        "documentFactCache": result.get("updated_fact_cache") or {},
    }
    existing_dossiers[esr_id] = dossier
    return dossier
