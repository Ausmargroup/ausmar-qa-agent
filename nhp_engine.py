"""
AUSMAR QA Agent — Stage 2: NHP Review QA engine.

Purpose
-------
After a PSE is accepted and estimating produces the NHP, this stage confirms that
every variation order (VO) in the signed NHP Changes document made it into the
Final NHP PDF that forms the contract price, with matching debit/credit amounts.

Inputs
------
- nhp_changes_path : PDF (or text) listing the VOs (additions/deletions/credits/debits)
- final_nhp_path   : the Final NHP pricing PDF that forms the contract price

Output (dict)
-------------
{
  "stage": 2,
  "deal_code": "...",
  "verdict": "PASS" | "REVIEW REQUIRED" | "FAIL",
  "verdict_reason": "...",
  "totals": {"vo_count": N, "matched": N, "mismatched": N, "missing": N,
             "reconciliation": {...}},
  "issues": [ {issue_ref, severity, category, section, signed_source,
               contract_output, discrepancy, required_action, status}, ... ],
  "vo_register": [...],            # extracted VOs
  "final_nhp_lines": [...],        # extracted pricing lines
  "needs_human_review": bool
}

Design principles (per the AUSMAR brief)
- Accuracy first: better to surface "REVIEW REQUIRED" than assert a false positive.
- Don't guess: ambiguous matches are flagged for human review, never silently passed.
- Deterministic maths: the AI extracts the registers, but dollar matching and total
  reconciliation are checked in Python so the verdict isn't at the mercy of LLM arithmetic.
"""

import json

import engine_common as ec
import db_v2


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
_VO_EXTRACT_SYS = """You are an AUSMAR estimating data extractor. You are given the raw text of a
signed NHP CHANGES document. It lists variation orders (VOs) — additions, deletions, credits, and debits
applied to a new home price.

Extract EVERY variation as a JSON object. Do not summarise, merge, or skip any line that carries a
dollar movement. If a value is unclear, set it to null and set "uncertain": true — never guess a number.

Return ONLY valid JSON:
{
  "deal_code": "string or empty",
  "vos": [
    {
      "vo_number": "string (e.g. VO-12 or 12, or empty if none shown)",
      "description": "string",
      "debit": number or null,   // amount ADDED to price (incl GST as shown)
      "credit": number or null,  // amount REMOVED/credited from price
      "page": number,            // page where it appears
      "uncertain": false
    }
  ]
}

Rules:
- A row may have either a debit or a credit (rarely both). Use null for the one not present.
- Keep amounts exactly as printed (GST-inclusive AUSMAR sell figures).
- Preserve the order they appear.
- Include superseded/overridden lines too, but keep their text verbatim so later logic can detect overrides."""


_NHP_EXTRACT_SYS = """You are an AUSMAR estimating data extractor. You are given the raw text of a
FINAL NHP pricing PDF (the document that forms the contract price). Extract every priced line item
that represents a variation/option (a debit or credit against the base price), plus the base price
and the grand total if shown.

Return ONLY valid JSON:
{
  "base_price": number or null,
  "grand_total": number or null,
  "lines": [
    {"description": "string", "debit": number or null, "credit": number or null, "page": number, "uncertain": false}
  ]
}

Rules:
- Do not invent items. If an amount is unreadable, set it null and "uncertain": true.
- Keep amounts as printed. Preserve order."""


def _extract_vos(pages):
    text = ec.pages_to_text(pages)
    raw = ec.call_text_model(_VO_EXTRACT_SYS, text, model="gpt-4.1-mini")
    obj = ec.parse_json_from_llm(raw)
    vos = obj.get("vos", []) if isinstance(obj, dict) else []
    deal_code = obj.get("deal_code", "") if isinstance(obj, dict) else ""
    # normalise amounts
    for v in vos:
        v["debit"] = ec.parse_money(v.get("debit"))
        v["credit"] = ec.parse_money(v.get("credit"))
    return deal_code, vos


def _extract_final_nhp(pages):
    text = ec.pages_to_text(pages)
    raw = ec.call_text_model(_NHP_EXTRACT_SYS, text, model="gpt-4.1-mini")
    obj = ec.parse_json_from_llm(raw)
    if not isinstance(obj, dict):
        obj = {}
    lines = obj.get("lines", [])
    for ln in lines:
        ln["debit"] = ec.parse_money(ln.get("debit"))
        ln["credit"] = ec.parse_money(ln.get("credit"))
    return {
        "base_price": ec.parse_money(obj.get("base_price")),
        "grand_total": ec.parse_money(obj.get("grand_total")),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Matching (deterministic + AI assist for description matching only)
# ---------------------------------------------------------------------------
_MATCH_SYS = """You are matching signed VARIATION ORDERS (VOs) to lines in the FINAL NHP pricing document.
For each VO, decide which final-NHP line (if any) corresponds to it, using description similarity and
amount. You MUST NOT force a match. If you are not confident, return matched_line_index = null and
confidence = "low" so a human can review it.

Respect the AUSMAR rules and exclusions provided.

Return ONLY valid JSON:
{
  "matches": [
    {
      "vo_index": number,                 // index into the VO list
      "matched_line_index": number|null,  // index into the final-NHP lines list, or null
      "confidence": "high"|"medium"|"low",
      "reason": "short explanation",
      "superseded": false                 // true if this VO is overridden by a later VO and intentionally absent
    }
  ]
}"""


def _match_vos(vos, nhp_lines, rules_block):
    payload = {
        "vos": [{"index": i, "description": v.get("description", ""),
                 "debit": v.get("debit"), "credit": v.get("credit")} for i, v in enumerate(vos)],
        "final_nhp_lines": [{"index": i, "description": l.get("description", ""),
                             "debit": l.get("debit"), "credit": l.get("credit")} for i, l in enumerate(nhp_lines)],
    }
    user = "Match the VOs to final NHP lines.\n\n" + json.dumps(payload, default=str) + rules_block
    raw = ec.call_text_model(_MATCH_SYS, user, model="gpt-4.1-mini")
    obj = ec.parse_json_from_llm(raw)
    return obj.get("matches", []) if isinstance(obj, dict) else []


def _vo_amount(v):
    """Signed net movement: debit positive, credit negative."""
    d = v.get("debit") or 0.0
    c = v.get("credit") or 0.0
    return d - c


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------
def run_nhp_review(nhp_changes_path, final_nhp_path, deal_code="", consultant_name="",
                   amount_tolerance=1.0, progress_cb=None):
    def _p(pct, msg):
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _p(5, "Reading NHP changes document...")
    changes_pages = ec.extract_pdf_pages(nhp_changes_path)
    _p(20, "Reading Final NHP pricing...")
    final_pages = ec.extract_pdf_pages(final_nhp_path)

    if not changes_pages or not final_pages:
        return {
            "stage": 2, "deal_code": deal_code, "verdict": "REVIEW REQUIRED",
            "verdict_reason": "One or both PDFs could not be read as text. Manual review required (document may be scanned/flattened).",
            "totals": {}, "issues": [], "vo_register": [], "final_nhp_lines": [],
            "needs_human_review": True,
        }

    _p(35, "Extracting variation orders...")
    extracted_deal, vos = _extract_vos(changes_pages)
    deal_code = deal_code or extracted_deal

    _p(55, "Extracting final NHP pricing lines...")
    final_nhp = _extract_final_nhp(final_pages)
    nhp_lines = final_nhp["lines"]

    _p(70, "Loading AUSMAR rules...")
    rules_block = db_v2.get_rules_prompt_block("Stage 2")

    _p(75, "Matching VOs to final NHP...")
    matches = _match_vos(vos, nhp_lines, rules_block) if vos else []
    match_by_vo = {m.get("vo_index"): m for m in matches if isinstance(m, dict)}

    # --- Build issues deterministically ---
    issues = []
    matched = 0
    mismatched = 0
    missing = 0
    issue_seq = 0

    def _ref(tag):
        nonlocal issue_seq
        issue_seq += 1
        code = deal_code or "NHP"
        return f"{code}-S2-{tag}-{issue_seq:03d}"

    for i, v in enumerate(vos):
        m = match_by_vo.get(i, {})
        line_idx = m.get("matched_line_index")
        superseded = bool(m.get("superseded"))
        conf = m.get("confidence", "low")
        vo_label = v.get("vo_number") or f"#{i+1}"
        desc = v.get("description", "")[:140]

        if superseded:
            matched += 1
            continue

        if line_idx is None or line_idx >= len(nhp_lines) or line_idx < 0:
            missing += 1
            issues.append({
                "issue_ref": _ref("VO"),
                "severity": "Critical",
                "category": "Pricing",
                "section": f"VO {vo_label}",
                "signed_source": f"VO {vo_label}: {desc} (signed changes p.{v.get('page','?')})",
                "contract_output": "No matching line found in Final NHP.",
                "discrepancy": "Signed VO appears to be missing from the Final NHP pricing.",
                "required_action": "Estimating to confirm this VO is in the Final NHP or provide written explanation. Verify before issuing contract price.",
                "status": "Open",
            })
            continue

        # matched — check amounts
        ln = nhp_lines[line_idx]
        vo_amt = _vo_amount(v)
        ln_amt = _vo_amount(ln)
        if v.get("debit") is None and v.get("credit") is None:
            # VO amount unknown — flag for review, do not assert
            issues.append({
                "issue_ref": _ref("VO"),
                "severity": "Medium",
                "category": "Pricing",
                "section": f"VO {vo_label}",
                "signed_source": f"VO {vo_label}: {desc}",
                "contract_output": f"Final NHP line: {ln.get('description','')[:120]}",
                "discrepancy": "VO amount could not be read with confidence; cannot verify it matches the Final NHP.",
                "required_action": "Human to confirm the VO dollar value matches the Final NHP line.",
                "status": "Open",
            })
            continue

        if abs(vo_amt - ln_amt) > amount_tolerance:
            mismatched += 1
            issues.append({
                "issue_ref": _ref("PRICE"),
                "severity": "High",
                "category": "Pricing",
                "section": f"VO {vo_label}",
                "signed_source": f"VO {vo_label}: {desc} — signed net ${vo_amt:,.2f}",
                "contract_output": f"Final NHP line '{ln.get('description','')[:90]}' — net ${ln_amt:,.2f} (p.{ln.get('page','?')})",
                "discrepancy": f"Amount mismatch of ${abs(vo_amt - ln_amt):,.2f} between signed VO and Final NHP.",
                "required_action": "Estimating to correct the Final NHP amount to match the signed VO, or provide approved reconciliation.",
                "status": "Open",
            })
        else:
            matched += 1
            if conf == "low":
                issues.append({
                    "issue_ref": _ref("VO"),
                    "severity": "Low",
                    "category": "Pricing",
                    "section": f"VO {vo_label}",
                    "signed_source": f"VO {vo_label}: {desc}",
                    "contract_output": f"Final NHP line: {ln.get('description','')[:120]}",
                    "discrepancy": "Amounts match but the description match is low-confidence.",
                    "required_action": "Human to confirm this is the correct corresponding line.",
                    "status": "Open",
                })

    # --- Total reconciliation (deterministic) ---
    recon = _reconcile(final_nhp, vos)
    if recon.get("checkable") and not recon.get("reconciles"):
        issues.append({
            "issue_ref": _ref("RECON"),
            "severity": "High",
            "category": "Pricing",
            "section": "NHP Total",
            "signed_source": f"Base ${recon['base']:,.2f} + net VO movement ${recon['vo_net']:,.2f} = expected ${recon['expected_total']:,.2f}",
            "contract_output": f"Final NHP grand total: ${recon['grand_total']:,.2f}",
            "discrepancy": f"Final NHP total is out by ${recon['delta']:,.2f} versus base + signed VOs.",
            "required_action": "Estimating to reconcile the Final NHP total against base price plus all signed VO movements.",
            "status": "Open",
        })

    # --- Verdict ---
    has_critical = any(x["severity"] == "Critical" for x in issues)
    has_high = any(x["severity"] == "High" for x in issues)
    needs_human = (not vos) or any(x["severity"] in ("Medium", "Low") for x in issues) or recon.get("checkable") is False

    if has_critical:
        verdict, reason = "FAIL", f"{missing} signed VO(s) appear missing from the Final NHP. Do not issue until resolved."
    elif has_high:
        verdict, reason = "REVIEW REQUIRED", f"{mismatched} amount mismatch(es) and/or total reconciliation issue found."
    elif needs_human:
        verdict, reason = "REVIEW REQUIRED", "All VOs matched, but some items need human confirmation (low-confidence matches, unreadable amounts, or totals not extractable)."
    else:
        verdict, reason = "PASS", f"All {len(vos)} signed VOs carried through to the Final NHP with matching amounts and reconciling total."

    _p(95, "Finalising...")
    return {
        "stage": 2,
        "deal_code": deal_code,
        "consultant_name": consultant_name,
        "verdict": verdict,
        "verdict_reason": reason,
        "totals": {
            "vo_count": len(vos),
            "matched": matched,
            "mismatched": mismatched,
            "missing": missing,
            "reconciliation": recon,
        },
        "issues": issues,
        "vo_register": vos,
        "final_nhp_lines": nhp_lines,
        "needs_human_review": needs_human or has_high or has_critical,
    }


def _reconcile(final_nhp, vos):
    base = final_nhp.get("base_price")
    grand = final_nhp.get("grand_total")
    if base is None or grand is None:
        return {"checkable": False,
                "note": "Base price and/or grand total could not be extracted from the Final NHP; total reconciliation skipped — confirm manually."}
    vo_net = sum(_vo_amount(v) for v in vos)
    expected = base + vo_net
    delta = grand - expected
    return {
        "checkable": True,
        "base": base,
        "vo_net": vo_net,
        "expected_total": expected,
        "grand_total": grand,
        "delta": delta,
        "reconciles": abs(delta) <= 1.0,
    }
