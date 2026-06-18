# AUSMAR QA Agent V2 — Architecture Specification

**Author:** Manus AI
**Date:** June 2026
**Context:** Expansion of the AUSMAR QA Agent from a single-stage PSE deposit tool into a 3-stage QA pipeline with self-learning rule management.

## 1. System Overview

The AUSMAR QA Agent V2 expands the existing Flask/Python web application to cover the entire sales-to-contract pipeline. It adds two new review stages and an admin-editable, self-learning rule library, while keeping the existing Stage 1 PSE QA functionality completely untouched. The system continues to run as a single Flask app on Railway (Docker + PostgreSQL in production, SQLite locally), using the OpenAI API for document analysis. Stage 1 remains the working deposit-submission tool; Stages 2 and 3 are additive and live in their own engine files and database tables so that nothing in the proven Stage 1 flow is modified.

### 1.1 Why three stages

The three stages map directly to the three points in the AUSMAR sales-to-contract process where document errors are introduced and where they are cheapest to catch. Stage 1 catches bad information entering the system at deposit. Stage 2 catches commercial variations that fail to carry from the signed NHP changes into the final NHP price. Stage 3 catches the contract pack errors — pricing, specification, and drawing-intent — before the pack reaches the client. Each stage answers a different question and controls a different risk, summarised below.

| Stage | Process position | Primary question | Main risk controlled |
|---|---|---|---|
| Stage 1: PSE Deposit QA (existing) | Deposit, before estimating | Is the submission complete, correct, signed, and buildable enough to proceed? | Bad information entering the system |
| Stage 2: NHP Review QA (new) | After PSE accepted, estimating produces NHP | Did every signed VO carry into the final NHP price with correct debit/credit? | Variations lost or mispriced before drafting |
| Stage 3: Pre-Contract QA (new) | After estimating/drafting, before client issue | Do the contract documents correctly reflect the signed NHP, VOs, and red pen? | Incorrect contract pack reaching the client |

### 1.2 Self-learning feedback loop

The defining feature of V2 is that the tool maintains itself without a developer or further Manus interaction. QA rules live in the database, not in hardcoded Python. Reviewers flag false positives with a categorised reason; admins review those false positives on a fortnightly cycle and convert them into rule exclusions with one click. Active rules and their exclusions are injected into the AI prompt context at run time, so the tool gets more accurate as it is used. This extends the pattern already proven in Stage 1, where the `_get_fp_notes` function injects staff-confirmed false positives into the prompt, and generalises it across all three stages.

## 2. Design Principles

The build is governed by a small set of non-negotiable principles drawn directly from the project constraints and from Nikole's stated preferences.

The first principle is that **accuracy beats coverage**. A false positive erodes staff trust faster than a missed issue, because once reviewers stop believing the flags they stop reading them. The engines are therefore tuned to flag only what they are confident about, and to route genuinely ambiguous items to a clearly-labelled "needs human review" bucket rather than asserting a defect. This is consistent with the guidance that the tool must not flag items covered by engineering, "as per plan" working-drawing notes, or wording differences that carry the same intent.

The second principle is **do not break Stage 1**. The existing `qa_engine.py`, the `reviews`/`feedback`/`prelogs` tables, and every existing route remain exactly as they are. New work is added in new files (`nhp_engine.py`, `contract_qa_engine.py`), new tables, and new routes. The shared helpers in `qa_engine.py` — the OpenAI client, PDF text and image extraction, and the LLM call wrappers — are imported and reused rather than duplicated, so there is a single source of truth for model access.

The third principle is **business ownership of the rules**. The value of the tool is AUSMAR-specific knowledge, not generic AI. Rules, severities, exclusions, and the change history are stored in the database and edited through the UI, so the process owner controls what the tool checks and how hard it pushes, with a full audit trail of who changed what.

## 3. Database Schema Extensions

The system supports both PostgreSQL (Railway production) and SQLite (local development) through the existing dual-backend pattern in `database.py`. There is no migration framework, so each new table and helper is added in both branches in the same hand-maintained style. All new tables are created with `CREATE TABLE IF NOT EXISTS` and are seeded idempotently so that an existing production database upgrades cleanly on the next deploy without touching existing rows.

### 3.1 Rule management tables

The `qa_rules` table is the editable rule library. Each row is one check the tool can perform, with a category, a human-readable description, a default severity, an active flag, and a stage applicability marker so a rule can apply to Stage 2, Stage 3, or both. The `rule_exclusions` table holds the known exceptions for each rule — the institutional-knowledge carve-outs that prevent the same false positive recurring. The `rule_history` table is the immutable audit trail recording every create, update, deactivation, and exclusion addition, including who made the change.

| Table | Key columns | Purpose |
|---|---|---|
| `qa_rules` | id, category, description, severity, active, stage_applicability, created_at, updated_at | The editable rule library that drives engine checks |
| `rule_exclusions` | id, rule_id (FK), exclusion_text, created_by, created_at | Known exceptions injected into prompts to suppress recurring false positives |
| `rule_history` | id, rule_id (FK), action, details (JSON), changed_by, created_at | Audit trail of every rule change |

### 3.2 Contract review tables

Stage 2 and Stage 3 reviews are stored separately from the PSE-specific `reviews` table to avoid overloading a schema that is tuned for deposit submissions. The `contract_reviews` table holds one row per Stage 2 or Stage 3 run, including the verdict and the full JSON result payload. The `contract_issues` table holds one row per discrepancy found, with the full issue record (severity, category, signed source, contract output, discrepancy, required action) and a status lifecycle so issues can move from Open through Fixed, Accepted Exception, or False Positive without losing history.

| Table | Key columns | Purpose |
|---|---|---|
| `contract_reviews` | id, deal_code, stage, consultant_name, job_category, status, verdict, verdict_reason, result_payload (JSON), created_at | One record per Stage 2/3 review |
| `contract_issues` | id, contract_review_id (FK), issue_ref, severity, category, section, signed_source, contract_output, discrepancy, required_action, status, created_at | One record per discrepancy with status lifecycle |

### 3.3 Pending reviews reuse

Stage 2 and Stage 3 reuse the existing `pending_reviews` table and its background-thread processing pattern. This keeps the async, poll-for-progress behaviour identical across all stages and avoids the Railway gateway timeout, since long PDF and vision analysis runs in a background thread while the browser polls a status endpoint.

## 4. Engine Architecture

Both new engines reuse the shared infrastructure already present in `qa_engine.py`: `get_client()` for the OpenAI client, `extract_pdf_text()` for text, `pdf_page_to_base64()` / `pdf_all_pages_to_base64()` for vision input, and `call_text_model()` / `call_vision_model()` / `parse_json_from_llm()` for model calls. Each engine produces a result payload that mirrors the Stage 1 convention — a top-level dict with `deal_code`, `checks`, `verdict_data`, and a flat issue list — so the frontend rendering and the database save paths stay consistent.

### 4.1 `nhp_engine.py` — Stage 2 NHP Review

Stage 2 takes two inputs: the NHP Changes document (the signed list of variation orders) and the Final NHP PDF that forms the contract price. The engine extracts a structured VO register from the changes document (VO number, description, debit, credit) and a pricing register from the final NHP, normalises item descriptions, and reconciles every VO against the final pricing. It reports each VO as matched, amount-mismatched, or missing, and validates amounts against the AUSMAR pricing formula where a cost basis is available. The output is an itemised discrepancy list keyed by VO, with debit/credit deltas shown explicitly.

The reconciliation is deterministic first and AI-assisted second. Exact and near-exact description matches and amount comparisons are done in Python so the numbers are trustworthy; the language model is used only for fuzzy description matching and for explaining ambiguous cases, never for inventing a dollar figure. This protects against the single most damaging failure mode, which is a confidently-stated but wrong credit amount.

### 4.2 `contract_qa_engine.py` — Stage 3 Pre-Contract QA

Stage 3 implements Lyana Rossow's full review in the order she works: NHP VOs against the NHP PDF, red pen markup against working drawings, setbacks and covenant requirements, and specification against working drawings. It runs as a two-pass model. Pass 1 is automated document logic — VO carry-through, debit/credit matching, credit traceability, deleted-item detection, pricing/spec contradictions, base-PSE protection, later-VO precedence, total reconciliation, electrical quantity reconciliation, and metadata consistency. Pass 2 is drawing-intent review using the vision model against the red pen and working drawings — elevation alignment, fixture positioning, deleted items on drawings, appliance clearances, electrical type accuracy, provision-versus-installed, annotation completeness, dimension changes, and lighting layout.

Job category drives review depth. CAT1 jobs (First Series / Super Saver) get the lightest pass; CAT4 jobs get the deepest, including the full set of high-error areas. The engine pays particular attention to the known top-error areas — Item 7 (Facade), Item 8 (Window modifications), and all square-metre quantity items such as tiling, concrete, and vinyl — and always verifies "as per plan" items, flagging where elaboration is needed (for example, a floating shelf where thickness is not specified). Active rules from `qa_rules` filtered to Stage 3 are applied, and their exclusions plus prior false positives are injected into the prompt context.

### 4.3 Output format

The Stage 3 output is itemised to match Lyana's existing template exactly. Findings are grouped first by the fifteen drawing pages — Site Plan, Floor Plan, Dimension Plan, Roof Plan, Elevations, Slab Plan, Sections, Kitchen, Ensuite, Bathroom / WC / Laundry, Electrical, Floor coverings, External Concrete, Landscaping, Details — and then by specification sections Item 1 through Item 26, with dot points under each heading. Every issue carries a severity (Critical, High, Medium, Low, Observation), a category (Pricing, Specification, Drawings/Elevations, Electrical, Wet Areas, Joinery, External), the signed source, the contract output, the discrepancy, and the required action, so each finding is directly actionable by the right team.

## 5. Self-Learning System Detail

The learning loop has four moving parts that together let the tool improve without code changes. When a reviewer sees a flag that is not a real issue, they mark it "Not an Issue" and choose a categorised reason — covered by engineering, standard practice / institutional knowledge, "as per plan" is acceptable here, wording difference only, superseded by later document, or a custom note. That feedback is stored against the issue. On the fortnightly cycle, the admin opens the Learning panel, which lists every false positive from the last fourteen days. With one click the admin can add the false positive as an exclusion condition on the relevant rule, adjust that rule's severity, or deactivate the rule entirely if it is consistently wrong. Every such action is written to `rule_history`. Finally, at run time, the engines pull the active rules and their exclusions and inject them into the AI prompt as known exceptions, so the next review already knows not to raise the suppressed finding.

| Mechanism | Trigger | Effect |
|---|---|---|
| False positive flagging | Reviewer marks a flag "Not an Issue" with a reason | Issue status set to False Positive; feedback recorded |
| Learning panel review | Admin fortnightly review | Convert FP to exclusion, adjust severity, or deactivate rule |
| Rule history | Any rule change | Immutable audit entry with actor and details |
| Prompt injection | Every Stage 2/3 run | Active exclusions and recent FPs added to AI context as known exceptions |

## 6. API and Frontend Extensions

New routes are added to `app.py` without altering existing ones. Stage 2 and Stage 3 reviews are initiated through dedicated endpoints, processed in background threads, and polled through the existing status endpoint pattern. The rule library, exclusions, and learning panel each have their own endpoints.

| Method and route | Purpose |
|---|---|
| `POST /api/review/stage2` | Start a Stage 2 NHP review (NHP Changes + Final NHP) |
| `POST /api/review/stage3` | Start a Stage 3 Pre-Contract review (full contract pack) |
| `GET /api/contract-reviews` and `GET /api/contract-reviews/<id>` | List and detail Stage 2/3 reviews |
| `GET/POST /api/rules`, `PATCH /api/rules/<id>` | Manage the rule library |
| `POST /api/rules/<id>/exclusions` | Add an exclusion to a rule |
| `GET /api/rules/<id>/history` | View a rule's change history |
| `GET /api/learning/false-positives` | List false positives from the last fortnight |
| `POST /api/contract-issues/<id>/status` | Update an issue's status (Fixed, Accepted Exception, False Positive) |

The frontend remains a single-file SPA. The "New Review" area gains a stage selector that swaps the upload fields for the chosen stage. An admin-only Rules panel lists and edits rules, a Learning panel surfaces recent false positives for one-click exclusion, and a "How to Use" page documents each stage, false-positive flagging, and the fortnightly rule review in language a non-technical user can follow.

## 7. Deployment and Constraints

The system stays on Railway as a Docker container backed by PostgreSQL, with SQLite for local development. The existing dependency set — `pypdf`, `pdf2image`, `openai`, `psycopg2-binary` — is sufficient, and the Dockerfile is updated only to copy the two new engine files into the image. Text extraction and comparison use `gpt-4.1-nano`; vision tasks against drawings and red pen use `gpt-4.1-mini`, matching the existing cost profile and keeping each review in the target range of roughly five to forty cents depending on document length and page count. Blocking logic mirrors the contract QA specification: a Stage 3 review cannot be marked approved for client issue while Critical issues remain open, and High issues require correction or a recorded manager exception, consistent with the AUSMAR rule that Josh's decision overrides at the exception level.

## 8. Implementation Sequence

The build proceeds in the order that protects Stage 1 and lets each layer be tested before the next depends on it: extend `database.py` with the new tables and seed the initial rule library; build `nhp_engine.py` for Stage 2; build `contract_qa_engine.py` for Stage 3; add the new routes to `app.py`; expand `index.html` with stage navigation, the rules and learning panels, and the documentation page; test locally end-to-end and confirm Stage 1 still passes; then update the README, requirements, and Dockerfile and push to GitHub to trigger the Railway deploy.
