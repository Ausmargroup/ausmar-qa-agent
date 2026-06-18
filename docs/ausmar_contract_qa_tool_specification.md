# AUSMAR Contract QA Tool — Specification and Framework

**Author:** Manus AI  
**Prepared for:** AUSMAR Homes  
**Working example:** S26JRGB2  
**Document purpose:** Internal specification and developer brief for a second-stage contract document QA system.

## 1. Tool purpose and positioning

The **AUSMAR Contract QA Tool** is a second-stage quality assurance tool that checks contract documents **coming out of estimating and drafting** before they are issued to the client for signing. Its job is to confirm that the final contract pack correctly reflects the signed NHP, signed variation orders, signed red pen markups, and any approved consultant/client changes.

This tool does **not** replace the existing PSE Submission QA tool. The existing PSE Submission QA tool checks the quality and completeness of paperwork **going in** at deposit stage. It asks whether the consultant has submitted the correct documents, whether the plan fits the lot, whether the red pen markup is signed and usable, and whether drafting/estimating have enough correct information to start work. The new Contract QA Tool asks a different question: **after estimating and drafting have completed their work, did the contract documents correctly capture what was signed?**

| QA control | Process position | Primary question | Main risk controlled | Example failure |
|---|---:|---|---|---|
| **PSE Submission QA** | Deposit stage, before drafting/estimating starts | Is the sales submission complete, correct, signed, and buildable enough to proceed? | Bad information entering the system | Missing GeoSite, wrong plan-to-lot fit, red pen not signed, documents named incorrectly |
| **Contract QA Tool** | Contract issue stage, after estimating/drafting and before client contract pack issue | Do the contract documents correctly reflect the signed NHP/PSE, signed VOs, and signed red pen changes? | Incorrect contract pack going to client | Signed credit missing, deleted item reintroduced, elevation not matching signed change, wrong electrical fixture type |

For S26JRGB2, the client signed an NHP with **61 variations**. Estimating and drafting then produced contract documents including the specification, NHP pricing, and working drawings. A first AI comparison found several pricing and text issues, including a PDR robe hook still charged when it should have been deleted, an ensuite recessed floor reintroduced after the VO removed it, a **$716 credit shortfall** on the TV antenna deletion, a PDR mirror contradiction, untraceable credits such as **$2,680 skylight**, **$895 porch posts**, and **$872 roof change**, plus pricing mismatches and electrical quantity reconciliation concerns. A second human/drawing review found additional items the AI missed, including elevation-level window height issues, pendants labelled incorrectly, laundry appliance clearance concerns, missing deleted shelf notation on elevation, ensuite mixer positioning errors, LED placement issues, wall light provision versus installed distinction, and a powerpoint type error.

The key operating principle is therefore simple: **one pass is not enough**. The tool must be designed around a **two-pass QA model** because automated document comparison and human drawing review catch different types of defects.

> **Operating rule:** Pass 1 catches document logic, pricing, credits, inclusions, exclusions, wording, and reconciliation. Pass 2 catches drawing intent, elevation accuracy, fixture positioning, clearances, buildability, and visual construction detail.

## 2. Required inputs

The tool requires the complete signed source-of-truth set and the complete contract output set. The signed source-of-truth set tells the system what should have been carried forward. The contract output set tells the system what estimating and drafting actually produced.

| Input group | Document | Required | Purpose in QA |
|---|---|---:|---|
| Source of truth | **Original signed PSE / NHP** | Yes | Establishes base home, facade, inclusions, price, site details, accepted scope, initial options, and baseline assumptions. |
| Source of truth | **Signed NHP changes document / signed variation orders** | Yes | Provides the official list of additions, deletions, credits, debits, and later overrides. For S26JRGB2, this includes 61 VOs. |
| Source of truth | **Signed red pen markup** | Yes | Captures drawing-level client intent, including structural changes, dimensions, electrical locations, fixture changes, elevations, and deleted/added notes. |
| Source of truth | Signed client selections, colours, electrical selection sheets, appliance selections | Conditional | Required where the contract pack includes selection-dependent specification, electrical, wet area, joinery, or appliance details. |
| Source of truth | Consultant notes and approved correspondence | Conditional | Required only where a signed variation references an external approval, clarification, or client decision. |
| Contract output | **Contract specification PDF** | Yes | Checked against signed changes for inclusions, exclusions, wording, contradictory notes, and item descriptions. |
| Contract output | **Contract NHP pricing PDF / contract price schedule** | Yes | Checked for pricing carry-through, debit/credit amounts, totals, traceability, and reconciliation. |
| Contract output | **Contract working drawings PDF** | Yes | Checked against signed red pen and VOs for elevations, dimensions, fixture positioning, annotations, electrical details, and deleted/reintroduced items. |
| Contract output | Electrical plan and electrical schedule | Conditional | Required if electrical appears inside the working drawings or as a separate document. Needed for quantity/type reconciliation. |
| Contract output | Engineering, slab, truss, energy, BAL, acoustic, estate covenant, or developer approval documents | Conditional | Required where these documents affect contract scope, cost, or construction obligations. |

The tool must reject or park a QA request if any mandatory document is missing. A contract-stage QA cannot reliably proceed if the signed source-of-truth documents are incomplete because the tool would be comparing contract documents against an uncertain baseline.

For S26JRGB2, the minimum upload set would be the signed original NHP/PSE, the signed NHP changes document showing all 61 VOs, the signed red pen markup, the contract specification PDF, the contract NHP pricing PDF, and the contract working drawings PDF. Any electrical selection schedule or client appliance selection should also be uploaded because several missed issues related to electrical fixture type, wall light provision, powerpoint type, and laundry appliance clearances.

## 3. Core operating model

The Contract QA Tool should run as a controlled checklist workflow, not as an open-ended AI chat. AI should be used to extract information, compare documents, identify conflicts, and draft discrepancy findings, but the process must be governed by predefined AUSMAR rules, category checklists, severity levels, sign-off steps, and an audit trail.

| Stage | Action | Owner | System role | Output |
|---|---|---|---|---|
| 1 | Upload source-of-truth and contract documents | Sales consultant or contracts admin | Validate file presence, naming, and version dates | Intake status: ready, parked, or rejected |
| 2 | Extract structured data | System | OCR, PDF text extraction, table extraction, drawing sheet indexing | Parsed job data and document map |
| 3 | Pass 1 automated comparison | System | Compare signed scope to contract spec/pricing and document metadata | Automated discrepancy report |
| 4 | Pass 2 drawing/elevation review | Drafting-capable reviewer using AI assistance | Review drawings, elevations, electrical plans, clearances, dimensions, and construction intent | Human-guided discrepancy report |
| 5 | Consolidate findings | System and reviewer | Merge duplicates, assign severity, category, owner, required action | Final QA report |
| 6 | Rectification loop | Estimating/drafting | Fix documents or provide written explanation | Updated contract pack or accepted exception |
| 7 | Final sign-off | Sales consultant, contracts admin, drafting/estimating as required | Lock QA record, store evidence, update deal stage | Approved for client issue |

The system should treat signed documents as the hierarchy of truth. Where documents conflict, the later signed variation should generally override the earlier base NHP item, unless AUSMAR has a specific rule stating otherwise. The tool must flag such conflicts rather than silently decide them.

| Source hierarchy | Document type | How the tool should treat it |
|---:|---|---|
| 1 | Latest signed VO / NHP change | Highest authority for the affected item, especially where it expressly deletes, credits, or replaces an earlier item. |
| 2 | Signed red pen markup | Highest authority for drawing intent, location, dimensions, and fixture placement where signed and legible. |
| 3 | Signed original PSE/NHP | Baseline contract scope and pricing before later changes. |
| 4 | Signed selections and approved correspondence | Supporting authority where it clarifies a specific selected product, fixture, appliance, finish, or location. |
| 5 | Contract output documents | Documents under test. They do not prove correctness by themselves. |

For S26JRGB2, the ensuite recessed floor issue is a clear example. If an earlier change or base note included the recessed floor, but a later signed VO removed it, the Contract QA Tool must treat the later signed deletion as controlling. If the contract specification or drawings reintroduced the recessed floor, the tool must flag it as a reintroduced deleted item.

## 4. Pass 1 — automated document comparison

Pass 1 is the automated AI-driven comparison layer. Its purpose is to check whether the contract specification and contract pricing have correctly carried forward every signed commercial and textual change from the signed NHP/PSE, signed VOs, and signed red pen markup.

Pass 1 should be deterministic where possible. It should not rely only on a language model making broad judgements. The preferred approach is to extract structured tables and item registers, normalise item names, apply AUSMAR-specific rules, then use AI reasoning for ambiguous wording, contradiction detection, and explanations.

### 4.1 Pass 1 checks

| Check area | What the tool checks | S26JRGB2 example |
|---|---|---|
| VO carry-through | Every signed VO appears in the contract pricing, contract specification, drawings, or is deliberately superseded by a later signed VO. | All 61 signed VOs must be present, superseded, or explained. |
| Debit and credit matching | Debit/credit amounts in the contract match the signed VO amounts, including GST treatment where applicable. | TV antenna deletion showed a **$716 credit shortfall**. |
| Credit traceability | Credits can be traced from signed deletion to contract price schedule and final total. | Untraceable credits included skylight **$2,680**, porch posts **$895**, and roof change **$872**. |
| Specification wording | Specification text reflects signed additions and deletions. Deleted items must not remain as live contract inclusions. | PDR robe hook still charged/listed when it should have been deleted. |
| Pricing/spec contradictions | Pricing and specification must not tell different stories. | PDR mirror appeared as removed in pricing but still listed in the specification. |
| Base PSE protection | Base inclusions from the signed PSE are not accidentally removed while processing later VOs. | If a base inclusion is missing from contract spec without a signed deletion, it is flagged. |
| Later VO precedence | Later signed VOs override earlier NHP items or previous changes. | Ensuite recessed floor reintroduced even though later VO removed it. |
| Pricing total reconciliation | Signed PSE base price plus accepted VOs equals contract price schedule and final contract value. | Tool must reconcile original NHP, 61 VO movements, credits, and final contract pricing. |
| Electrical quantity reconciliation | Electrical item quantities and types reconcile between signed selections, pricing, and drawings. | AI pass flagged electrical quantity reconciliation issues. |
| Metadata consistency | Names, lot, address, estate, facade, series, plan, consultant, and deal code align across all documents. | S26JRGB2 contract pack should consistently show the same client/job identifiers. |

### 4.2 Pass 1 data extraction registers

The tool should convert documents into structured registers before comparison. These registers become the working data layer for rules, AI checks, reports, and audit history.

| Register | Key fields | Source documents | Use |
|---|---|---|---|
| Deal register | Deal code, client names, lot, address, estate, plan, facade, series, consultant | PSE/NHP, contract documents, HubSpot | Confirms all documents belong to the same job. |
| VO register | VO number, description, category, debit, credit, GST flag, signed date, superseded status | Signed NHP changes document | Master list of changes that must carry through. |
| Pricing register | Line item, amount, debit/credit, category, page reference, matched VO | Contract NHP pricing PDF | Reconciles signed VOs to contract pricing. |
| Specification register | Section, clause, inclusion/exclusion, item description, page reference, matched VO | Contract specification PDF | Detects deleted items, missing inclusions, and contradictions. |
| Drawing index | Sheet number, sheet name, revision, page number, discipline | Working drawings PDF | Directs Pass 2 reviewers to affected sheets. |
| Electrical register | Fixture type, quantity, location, mounting height, provision/install status, page reference | Electrical plan, specification, pricing, red pen | Reconciles electrical quantities and item types. |
| Exception register | Issue ID, rule triggered, evidence, severity, owner, status | System-generated | Drives rectification and sign-off. |

For S26JRGB2, the VO register would include 61 signed changes. The pricing register would need to identify whether each VO has a matching pricing line, whether the amount matches, and whether later VOs supersede earlier changes. The specification register would then be used to confirm that items deleted commercially are also removed from the specification wording, not just credited in pricing.

### 4.3 Pass 1 rules

Pass 1 should use a rules engine with clear pass/fail logic. AI can assist with fuzzy matching, but the rules should define what counts as a problem.

| Rule ID | Rule | Severity default | Example |
|---|---|---:|---|
| P1-VO-001 | Every signed VO must be matched to a contract output item or marked as superseded by a later signed VO. | High | A signed skylight deletion has no traceable credit or spec update. |
| P1-PRICE-002 | Contract debit/credit amount must equal the signed VO amount unless a documented adjustment exists. | High | TV antenna credit is short by **$716**. |
| P1-SPEC-003 | If a signed VO deletes an item, the item must not remain as a live inclusion in the specification. | High | PDR robe hook remains listed after deletion. |
| P1-CONTRA-004 | Pricing, specification, and drawings must not contradict each other on the same item. | High | PDR mirror credited as removed but still included in spec. |
| P1-BASE-005 | Base PSE inclusions must not be removed unless there is a signed deletion or substitution. | Medium/High | Base allowance disappears during contract preparation. |
| P1-SUPER-006 | Later signed VOs override earlier versions of the same item. | High | Ensuite recessed floor removed by later VO but reappears in contract. |
| P1-META-007 | Client names, address, lot, plan, facade, series, and deal code must match across all documents. | Medium/High | Contract spec shows a different facade than pricing. |
| P1-ELEC-008 | Electrical quantities in pricing must reconcile to the electrical plan and signed selections. | Medium/High | Quantity of wall lights differs between pricing and drawing. |

Severity should be rule-driven but adjustable by the reviewer. A dollar discrepancy above an agreed threshold, a client-facing contradiction, or any item likely to cause contract reissue should default to **High**. Anything that could create construction rework, client dispute, contract variation, or margin leakage should be **Critical** or **High**.

## 5. Pass 2 — drawing/elevation review with AI assistance

Pass 2 is the human-guided drawing and elevation review. Its purpose is to catch the issues that are visible in construction drawings but may not be obvious from pricing and specification text. This is the pass that protects construction intent.

The S26JRGB2 result proves this pass is mandatory. The AI comparison caught pricing, credit, and spec text discrepancies, but the human consultant review caught elevation-level and installation-intent issues that were missed by the AI, including window head alignment, pendant/wall light mislabelling, mixer positioning, appliance clearance, missing elevation notes, LED placement, provision versus installed wording, and single versus double GPO type.

Pass 2 should not be a general visual scan. It should be a guided review that uses the signed red pen markup, the VO register, and the drawing index to direct the reviewer to the exact sheets and issue types that need checking.

### 5.1 Pass 2 checks

| Check area | What the reviewer checks | S26JRGB2 example |
|---|---|---|
| Elevation alignment | Window and door heights match elevations and each other where required by design intent or signed markup. | Window height did not match door head on elevation. |
| Fixture positioning | Mixers, lights, powerpoints, niches, shelves, hooks, and other fixtures are placed where signed. | Ensuite mixer positioning errors. |
| Deleted items on drawings | Items deleted by signed VO or red pen are removed from floor plans, elevations, electrical plans, and notes. | Deleted shelf note missing from elevation. |
| Appliance clearances | Laundry, kitchen, garage, and wet-area appliances have practical clearance and opening space. | Laundry appliance clearance concerns. |
| Electrical type accuracy | Electrical symbols and labels correctly distinguish pendant, wall light, LED, provision, installed fitting, single GPO, and DGPO. | Pendants should have been wall lights with specific heights; powerpoint type wrong, single versus DGPO. |
| Provision versus installed | Drawings and pricing distinguish between rough-in/provision only and full supply/install. | Wall light provision versus installed distinction. |
| Note/annotation completeness | Required notes from signed changes are visible on relevant sheets and elevations. | Missing deleted shelf note/elevation annotation. |
| Dimension changes | Dimensions reflect signed reductions, extensions, or deleted sections. | 400mm removed from ensuite must show correctly in floor plan and wet area details. |
| Lighting layout | LED positions align with signed electrical intent and practical room layout. | LED placement issues. |

### 5.2 Pass 2 drawing review workflow

| Step | Review action | Tool assistance | Human decision required |
|---:|---|---|---|
| 1 | Identify affected drawing sheets from signed VOs and red pen markup. | Sheet indexing and keyword matching. | Confirm relevant sheets have been selected. |
| 2 | Compare red pen locations to contract drawings. | AI highlights likely changed rooms, fixtures, dimensions, and notes. | Confirm if the drawing correctly reflects signed intent. |
| 3 | Review elevations for changed openings, deleted items, facade changes, heights, and notes. | Side-by-side visual comparison and issue checklist. | Decide if elevation discrepancy is material. |
| 4 | Review electrical plan against electrical selections and pricing. | Quantity/type extraction and symbol list comparison. | Confirm installed/provision and fixture type accuracy. |
| 5 | Review wet areas and joinery for dimensions, mixer locations, shelves, niches, mirrors, hooks, clearances, and appliance spaces. | Room-specific checklists. | Confirm buildability and client intent. |
| 6 | Record discrepancy with evidence and required action. | Structured issue form, screenshot capture, page reference. | Assign severity and owner. |

The tool should support a side-by-side view of the signed red pen sheet and the corresponding contract working drawing sheet. The reviewer should be able to mark a drawing region, attach a screenshot, write the issue, classify the issue category, and assign it to drafting, estimating, sales, or contracts admin.

### 5.3 Pass 2 rules and prompts

Pass 2 should be supported by structured prompts and rules rather than leaving the reviewer to remember every possible issue.

| Review trigger | Required drawing check | Example prompt shown to reviewer |
|---|---|---|
| VO or red pen changes a window/door | Check plan, elevation, schedule, and head height. | “Does the window height/head height match the signed markup and adjacent door head where intended?” |
| VO deletes a fixture or shelf | Check floor plan, elevation, wet-area detail, joinery detail, and notes. | “Is the deleted item removed from every drawing view and annotation?” |
| VO changes lighting | Check electrical symbol, label, quantity, switching, mounting height, and provision/install wording. | “Is this a pendant, wall light, LED, or provision only? Does the label match the signed instruction?” |
| VO changes wet area dimensions | Check plan dimension, fixture spacing, mixer position, tile/wet-area notes, and elevations. | “Does the wet area still work after the signed dimension change?” |
| Appliance selection affects cabinetry | Check appliance width, depth, opening, door swing, clearance, and any required note. | “Is there enough clearance for the selected appliance and normal use?” |

For S26JRGB2, if a signed change showed wall lights at specific heights, the tool should not simply confirm that “lights” exist. It must force the reviewer to confirm fixture **type**, **height**, **location**, and **provision/install status**. This is how the tool avoids the pendant-versus-wall-light error and the provision-versus-installed distinction.

## 6. Output format

The output must be a practical discrepancy report that estimating, drafting, contracts admin, and the sales consultant can act on immediately. It must not be a generic AI summary. Each discrepancy must show what was signed, what the contract says, what is wrong, and the required action.

### 6.1 Severity levels

| Severity | Meaning | Action requirement | Example |
|---|---|---|---|
| **Critical** | Contract should not be issued. Issue may create incorrect contract value, construction error, client dispute, compliance risk, or major rework. | Must be fixed or formally accepted by manager before issue. | Later signed VO deletes an item but contract reintroduces it; material credit missing. |
| **High** | Contract likely needs correction before client issue. Commercial, specification, or drawing discrepancy is material. | Must be resolved before issue unless approved exception. | $716 credit shortfall; PDR mirror contradiction; wrong fixture type. |
| **Medium** | Issue may confuse client, cause drafting/estimating rework, or create construction ambiguity. | Resolve where practical before issue; document if deferred. | Missing drawing note, unclear provision/install wording. |
| **Low** | Minor wording, formatting, or administrative inconsistency with low commercial/build risk. | Fix if easy; can proceed if not material. | Minor label inconsistency not affecting scope, price, or construction. |
| **Observation** | Not a defect, but a reviewer note or risk to monitor. | No mandatory correction unless manager decides. | Appliance clearance looks tight but may be acceptable if confirmed. |

### 6.2 Issue categories

The report should group issues into the categories requested by AUSMAR. This helps each team focus on the items they can fix.

| Category | Typical owner | Examples |
|---|---|---|
| **Pricing** | Estimating | Missing VO, incorrect debit/credit, untraceable credit, total not reconciling. |
| **Specification** | Estimating/contracts admin | Deleted item still listed, addition missing, contradictory spec wording. |
| **Drawings/Elevations** | Drafting | Window height, elevation note, deleted item still shown, dimension mismatch. |
| **Electrical** | Drafting/estimating/electrical selections | Wrong fixture type, incorrect quantity, provision versus installed, single GPO versus DGPO. |
| **Wet Areas** | Drafting/estimating | Mixer position, recessed floor, niche/shelf, mirror, robe hook, ensuite dimension. |
| **Joinery** | Drafting/estimating | Laundry appliance clearance, cabinetry clearances, shelf deletion, appliance integration. |
| **External** | Drafting/estimating | Porch posts, roof change, facade/elevation, skylight, external deletion/credit. |

### 6.3 Required issue fields

| Field | Requirement | Example using S26JRGB2 |
|---|---|---|
| Issue ID | Unique ID generated by system. | S26JRGB2-P1-PRICE-003 |
| Severity | Critical, High, Medium, Low, Observation. | High |
| Category | Pricing, Specification, Drawings/Elevations, Electrical, Wet Areas, Joinery, External. | Pricing |
| Signed source | What the client signed, with document/page/VO reference. | Signed VO deletes TV antenna and should apply full credit. |
| Contract output | What the contract pack currently says, with document/page reference. | Contract pricing applies credit short by **$716**. |
| Discrepancy | Clear statement of the problem. | Credit does not match signed deletion amount. |
| Required action | Exact correction required. | Update contract pricing to apply the missing **$716** credit or provide approved written explanation. |
| Owner | Estimating, drafting, contracts admin, sales consultant, manager. | Estimating |
| Status | Open, in review, fixed, accepted exception, not applicable. | Open |
| Evidence | Page references, screenshots, extracted text, calculation trail. | VO page X; contract pricing page Y; reconciliation table. |

### 6.4 Example report entries for S26JRGB2

| Issue ID | Severity | Category | What was signed | What the contract says | What is wrong | Required action |
|---|---|---|---|---|---|---|
| S26JRGB2-P1-PRICE-001 | High | Pricing | Signed change deleted TV antenna with expected full credit. | Contract pricing applies a lesser credit. | **$716 credit shortfall**. | Estimating to correct credit or provide approved reconciliation. |
| S26JRGB2-P1-SPEC-002 | High | Specification / Wet Areas | PDR robe hook deleted. | PDR robe hook still appears charged/listed. | Deleted item remains live in contract documents. | Remove robe hook from spec/pricing or explain if superseded by later signed VO. |
| S26JRGB2-P1-WET-003 | High | Wet Areas | Later VO removed ensuite recessed floor. | Contract reintroduced recessed floor. | Earlier/deleted item carried back into contract. | Remove recessed floor from contract documents and confirm drawings/spec match. |
| S26JRGB2-P1-SPEC-004 | High | Specification / Pricing | PDR mirror treatment changed/deleted. | Pricing and specification conflict. | Client-facing contradiction. | Align pricing and specification to signed source. |
| S26JRGB2-P1-PRICE-005 | High | Pricing / External | Signed credits exist for skylight, porch posts, roof change. | Credits are not traceable in contract pricing. | Unclear whether client received correct credit. | Create traceable reconciliation for **$2,680**, **$895**, and **$872** credits. |
| S26JRGB2-P2-ELEV-006 | High | Drawings/Elevations | Signed drawings require window/door head alignment. | Elevation shows window height not matching door head. | Elevation does not reflect design intent. | Drafting to amend elevation or confirm approved exception. |
| S26JRGB2-P2-ELEC-007 | High | Electrical | Signed markup requires wall lights at specific heights. | Contract drawing labels them as pendants. | Wrong fixture type and likely wrong construction instruction. | Change pendant labels to wall lights and show required heights. |
| S26JRGB2-P2-JOIN-008 | Medium/High | Joinery | Laundry appliance arrangement requires workable clearance. | Contract drawings raise clearance concern. | Potential appliance usability/buildability issue. | Drafting to verify appliance dimensions and clearance; amend if required. |
| S26JRGB2-P2-WET-009 | High | Wet Areas | Signed ensuite layout requires correct mixer positions. | Contract drawing shows mixer positioning errors. | Wet area construction intent not captured. | Amend wet area elevations/details to signed mixer locations. |
| S26JRGB2-P2-ELEC-010 | High | Electrical | Signed electrical requires correct powerpoint type. | Contract drawing shows single where DGPO required, or vice versa. | Wrong electrical item type. | Correct symbol/label and reconcile pricing if required. |

### 6.5 Consultant summary

The report should also include a short consultant-facing summary. This is not an executive summary; it is an action summary for the person who needs to drive the corrections.

| Summary item | Required content |
|---|---|
| Contract issue recommendation | “Do not issue”, “Issue after listed corrections”, or “Issue approved with exceptions”. |
| Open critical/high items | Count and list by owner. |
| Pricing exposure | Total known dollar discrepancy and total untraceable credit/debit value. |
| Drawing risk | List construction-intent issues requiring drafting confirmation. |
| Required next step | Who must fix what before client issue. |

For S26JRGB2, the likely recommendation would be **do not issue until high-priority pricing/spec contradictions and drawing/electrical issues are resolved**. The consultant summary should identify estimating actions for credit/pricing/spec items and drafting actions for elevations, electrical labels, mixer positions, LED placements, and appliance clearance.

## 7. Sign-off workflow

The tool must include sign-off because QA without enforced resolution becomes another report people can ignore. The default position should be that a contract pack cannot move to client issue while Critical issues are open. High issues should also block issue unless a manager approves an exception.

| Step | Owner | Required action | System status |
|---:|---|---|---|
| 1 | Sales consultant or contracts admin | Submit contract pack for QA. | QA submitted |
| 2 | System | Complete Pass 1 and produce automated findings. | Pass 1 complete |
| 3 | Human reviewer | Complete Pass 2 and add drawing/elevation findings. | Pass 2 complete |
| 4 | Sales consultant | Review consolidated report and assign corrections. | Corrections requested |
| 5 | Estimating/drafting | Fix issues or provide written response. | In rectification |
| 6 | Reviewer | Verify corrected documents. | Recheck complete |
| 7 | Sales consultant and contracts admin | Confirm client pack can be issued. | Approved for client issue |
| 8 | System | Lock report, retain evidence, update HubSpot. | QA closed |

Accepted exceptions must be rare and visible. An accepted exception should require the approver’s name, date, reason, residual risk, and evidence. For example, if a drawing note appears different from red pen because drafting used an equivalent construction notation, that can be accepted if the reviewer confirms it is genuinely equivalent.

## 8. Technical architecture

The recommended build is a secure web application with document intake, document parsing, a rules engine, AI-assisted comparison, human drawing review screens, report generation, HubSpot integration, and an audit trail.

| Layer | Recommended component | Purpose |
|---|---|---|
| User interface | Internal web app | Upload documents, view comparison results, complete Pass 2 checklist, assign actions, sign off. |
| Intake service | File validation and version control | Confirms required files are present, records upload time, stores original documents. |
| Document storage | Secure cloud file storage | Stores source documents, contract outputs, extracted text, screenshots, and final reports. |
| Parsing pipeline | PDF text extraction, OCR, table extraction, drawing sheet indexing | Converts PDFs into structured data and searchable page references. |
| AI reasoning layer | Commercial API-based multimodal reasoning | Performs fuzzy matching, contradiction detection, clause interpretation, drawing-assist review, and discrepancy drafting. |
| Rules engine | AUSMAR-specific rule database | Applies deterministic QA rules, severity defaults, category mapping, and blocking logic. |
| Application database | Relational database | Stores deals, documents, VOs, extracted registers, issues, comments, status, and audit trail. |
| Reporting service | Markdown/PDF/HTML report generator | Produces discrepancy reports for estimating, drafting, contracts, and sales. |
| HubSpot integration | Deal stage, task, note, and attachment sync | Triggers QA, updates status, creates actions, and stores final report against the deal. |
| Audit trail | Immutable event log | Records who uploaded, reviewed, changed, approved, or accepted exceptions. |

The system should use commercial AI APIs rather than consumer chat accounts. Current API pricing is generally usage-based per million tokens. OpenAI lists flagship API rates such as GPT-5.4 at **US$2.50 per 1M input tokens** and **US$15.00 per 1M output tokens**, with Batch API available at a **50% input/output discount** for asynchronous work.[1] Anthropic lists Claude Sonnet 4.6 at **US$3 per million input tokens** and **US$15 per million output tokens**, with caching and batch pricing options.[2] Google lists Gemini 3.5 Flash paid tier pricing at **US$1.50 per 1M input tokens** and **US$9.00 per 1M output tokens**, with batch and flex options also available.[3]

### 8.1 Recommended architecture pattern

| Component | MVP requirement | Full system requirement |
|---|---|---|
| Web app | Upload files, run QA, view report, mark status. | Full dashboard, side-by-side review, screenshots, comments, permissions, version comparison. |
| AI pipeline | Text extraction plus AI comparison against source documents. | Multi-model pipeline with OCR, table extraction, drawing vision, model fallback, confidence scoring. |
| Rules engine | Core VO/pricing/spec rules and severity mapping. | Full AUSMAR rules library by category, plan series, facade, MPA triggers, standard inclusions, electrical rules, wet-area rules. |
| Pass 2 review | Manual checklist with page references. | Interactive drawing viewer with signed red pen overlay, AI-highlighted suspect areas, issue capture. |
| Reporting | Markdown/PDF report attached to deal. | Role-specific reports, trend dashboards, rework analytics, recurring issue analysis. |
| HubSpot | Manual deal code entry and report upload. | Automatic trigger by deal stage, task creation, status syncing, final sign-off record. |

### 8.2 Document parsing approach

The parsing pipeline should process documents in a predictable order.

| Step | Method | Output |
|---:|---|---|
| 1 | Split PDFs into pages and identify document type. | Document index with page numbers and page thumbnails. |
| 2 | Extract embedded PDF text where available. | Raw text by page. |
| 3 | Run OCR on scanned pages and red pen markups. | OCR text with confidence scores. |
| 4 | Extract pricing and VO tables. | Structured VO/pricing rows. |
| 5 | Identify drawing sheets, revisions, and sheet names. | Drawing index. |
| 6 | Extract key labels, notes, dimensions, and symbols from drawings where feasible. | Drawing annotation register. |
| 7 | Normalise terminology. | Matched terms such as DGPO/double GPO, powder room/PDR, ensuite/ENS, wall light/WL. |
| 8 | Run rule checks and AI comparisons. | Issues, warnings, and confidence scores. |

The system should store the extracted data separately from the source PDFs. This allows rechecking after new documents are uploaded and allows AUSMAR to analyse recurring errors over time.

### 8.3 AUSMAR-specific rules database

The value of this tool will come from AUSMAR-specific knowledge, not generic AI. The rules database should encode standard inclusions, plan terminology, common abbreviation mappings, MPA triggers, estimating rules, drafting review rules, and known high-risk issue types.

| Rule group | Examples of encoded knowledge |
|---|---|
| Standard inclusions | Base home inclusions, common PSE items, standard fixture assumptions, plan/facade inclusions. |
| Variation logic | Later VO overrides earlier VO, deletion must remove spec/drawing references, credits must reconcile. |
| MPA / approval triggers | Structural change, facade change, estate/covenant issue, window/door change, external appearance change, acoustic/BAL relevance. |
| Electrical terminology | Pendant vs wall light, LED/downlight, provision only vs installed, single GPO vs DGPO, mounting height notes. |
| Wet area rules | Recessed floor, mixer locations, niche/shelf/mirror/hook, dimension changes, waterproofing-sensitive changes. |
| Joinery rules | Appliance clearance, shelf deletion, cabinetry appliance integration, laundry/kitchen space checks. |
| External/facade rules | Roof changes, porch posts, skylights, window/door head heights, elevation consistency. |
| Severity rules | Dollar thresholds, client-facing contradictions, construction impact, contract reissue likelihood. |

For S26JRGB2, the rules database would need to understand that “PDR” means powder room, that a mirror or robe hook can appear in pricing, specification, and drawings, that a later deletion must remove the item from all contract outputs, and that a wall light provision is not the same as an installed wall light.

### 8.4 Audit trail

The audit trail should record every significant event. This is important because contract QA affects client-facing documents and internal accountability.

| Event | Data recorded |
|---|---|
| File uploaded | User, timestamp, document type, file name, version, checksum. |
| QA run started | User, deal code, selected documents, rules version, AI model version. |
| AI finding generated | Issue ID, source evidence, model, prompt version, confidence score. |
| Human issue added | Reviewer, screenshot/page reference, category, severity, required action. |
| Issue edited | Previous value, new value, editor, timestamp. |
| Issue resolved | Fix description, corrected document version, verifier, timestamp. |
| Exception approved | Approver, reason, residual risk, timestamp. |
| QA closed | Final status, sign-off users, report version, HubSpot update. |

## 9. Workflow integration

The Contract QA Tool should be triggered after estimating and drafting have produced the contract pack, but before the pack is sent to the client. The process should be integrated into HubSpot so that QA is not dependent on someone remembering to run it manually.

| Workflow point | Recommended approach |
|---|---|
| Trigger | Automatic when deal moves to a “Contract Pack Ready for QA” stage, with manual trigger available for rechecks. |
| Submitter | Contracts admin or sales consultant uploads/links final contract specification, NHP pricing, working drawings, and signed source documents. |
| Pass 1 owner | System-generated; sales consultant/contracts admin reviews commercial findings. |
| Pass 2 owner | Drafting-capable reviewer, senior sales consultant, or trained contract QA reviewer. |
| Rectification owner | Estimating for price/spec issues; drafting for drawings/elevations/electrical layouts; sales for client clarification. |
| Sign-off owner | Sales consultant confirms client intent; contracts admin confirms pack readiness; manager approves any exception. |
| HubSpot output | QA status, issue count, blockers, final report attachment, tasks for estimating/drafting, and signed-off date. |

### 9.1 Suggested HubSpot deal stages/properties

| HubSpot field/stage | Purpose |
|---|---|
| Contract Pack Ready for QA | Triggers QA process. |
| Contract QA Status | Not started, Intake parked, Pass 1 running, Pass 2 required, Corrections required, Recheck required, Approved, Approved with exception. |
| Contract QA Critical Count | Blocks client issue if greater than zero. |
| Contract QA High Count | Requires correction or approved exception. |
| Contract QA Last Run Date | Audit and workflow control. |
| Contract QA Report Link | Stores final report. |
| Contract QA Sign-off Date | Confirms pack can be issued. |
| Contract QA Exception Approved By | Records exception approver where relevant. |

The HubSpot integration should create tasks automatically when discrepancies are found. For example, S26JRGB2 would generate estimating tasks for the TV antenna credit, skylight/porch/roof credit traceability, PDR robe hook, PDR mirror contradiction, and ensuite recessed floor reintroduction. It would generate drafting tasks for window/door elevation alignment, wall light labels and heights, appliance clearance, shelf note, mixer positions, LED placement, and GPO type.

## 10. Implementation plan

The practical implementation should be staged. The aim is not to build the perfect system first. The aim is to build an MVP that catches the high-value errors quickly, then expand the rules and drawing-review capability as AUSMAR collects more benchmark jobs.

| Phase | Timeline | Scope | Dependencies | Output |
|---:|---|---|---|---|
| 1 | 2–3 weeks | Discovery and document mapping. Confirm document types, HubSpot stages, file naming, current contract issue process, and top recurring error types. | Sample historical jobs, stakeholder access, current templates. | Final build brief, data map, MVP rules list. |
| 2 | 4–6 weeks | MVP Pass 1. Upload documents, extract VO/pricing/spec data, reconcile signed VOs to contract pricing/spec, produce discrepancy report. | OCR/table extraction tooling, AI API access, sample jobs. | Working MVP for automated document comparison. |
| 3 | 3–5 weeks | Pass 2 guided review. Add drawing index, room/category checklists, side-by-side review, issue capture, severity/category workflow. | Drafting reviewer input, drawing samples, red pen examples. | Human-guided drawing review module. |
| 4 | 2–4 weeks | HubSpot integration and sign-off workflow. Add deal triggers, status fields, tasks, report links, exception approval. | HubSpot admin access, agreed fields/stages. | Integrated QA workflow. |
| 5 | 4–8 weeks | Testing and rule hardening. Run historical jobs, measure missed/caught issues, refine rules, build benchmark dataset. | Historical contract packs and known issue outcomes. | Tested rules library and go-live recommendation. |
| 6 | Ongoing | Full system expansion. Trend analytics, recurring error dashboards, standard inclusion library, MPA triggers, model fallback, improved drawing AI. | Production usage data. | Continuous improvement program. |

### 10.1 MVP scope

The MVP should focus on the problems most likely to cost money, delay contract issue, or damage client trust.

| MVP inclusion | Reason |
|---|---|
| Mandatory document intake checklist | Prevents incomplete QA runs. |
| VO register extraction | Every signed change needs to be tracked. |
| Pricing reconciliation | Direct margin and client trust risk. |
| Credit traceability | S26JRGB2 showed multiple credits that needed tracing. |
| Specification deletion/addition check | Prevents contradictions and deleted items remaining live. |
| Metadata consistency | Prevents wrong job/client/lot/plan details in contract pack. |
| Basic electrical quantity/type reconciliation | S26JRGB2 showed electrical risk across both passes. |
| Guided Pass 2 drawing checklist | Captures issues AI alone misses. |
| Severity/category report | Makes the result actionable. |
| Sign-off status | Stops unresolved issues being ignored. |

### 10.2 Later-stage scope

| Later feature | Why it should wait |
|---|---|
| Full visual overlay of red pen versus contract drawings | Valuable but more complex; needs stable drawing indexing first. |
| Automated dimension measurement from drawings | High value but requires careful validation because PDF scale, scanning, and sheet quality can vary. |
| Automatic MPA/developer approval trigger checks | Requires AUSMAR-specific rule library and approval process mapping. |
| Trend analytics by estimator/drafter/consultant/category | Best built after enough production QA data exists. |
| Client-facing correction summaries | Should only be introduced after internal wording and liability controls are approved. |

### 10.3 Testing approach

Testing should use historical jobs with known outcomes. S26JRGB2 should be the first benchmark case because it contains both AI-detectable and human-detectable issues.

| Test type | Method | Pass condition |
|---|---|---|
| Golden job replay | Run S26JRGB2 through the tool and compare against known Pass 1 and Pass 2 findings. | Tool identifies all known high-risk issues or routes them to human review. |
| Historical sample test | Run 10–20 past jobs with known contract corrections. | Tool catches agreed percentage of historical material errors. |
| False-positive test | Review how many flagged issues are not real discrepancies. | False positives are low enough that staff still trust the tool. |
| Recheck test | Upload corrected documents and confirm issues close correctly. | Fixed issues are not repeatedly flagged without reason. |
| Workflow test | Confirm HubSpot status, tasks, reports, and sign-off behave correctly. | No deal can be approved with unresolved Critical items. |
| User acceptance test | Sales, estimating, drafting, and contracts admin complete sample reviews. | Users can complete QA without developer support. |

## 11. Cost and resource requirements

Costs depend on whether AUSMAR builds this as an internal lightweight tool, a robust production web app, or a fully integrated HubSpot-connected QA platform. The estimates below are practical ranges for scoping, not fixed quotes.

### 11.1 Development cost estimate

| Build option | Likely cost range | What AUSMAR gets | Fit |
|---|---:|---|---|
| Lightweight prototype | **A$8,000–A$20,000** | Manual upload, automated Pass 1 report, basic rules, no deep HubSpot integration, limited drawing review. | Good for proving value quickly on S26JRGB2 and 5–10 historical jobs. |
| MVP production tool | **A$35,000–A$75,000** | Secure web app, document intake, Pass 1 reconciliation, guided Pass 2 review, report workflow, basic sign-off, limited HubSpot integration. | Recommended starting build. |
| Full integrated platform | **A$90,000–A$180,000+** | Full HubSpot automation, robust audit trail, advanced drawing viewer, rules database, analytics, role permissions, exception approvals. | Best after MVP proves catch rate and workflow value. |

The recommended path is to build the **MVP production tool**, not a prototype that becomes throwaway work and not a full platform before the rules are proven. S26JRGB2 demonstrates that the core value is already clear: the tool would have identified both commercial discrepancies and drawing/construction-intent issues before client issue.

### 11.2 Ongoing running costs

| Cost item | Expected range | Notes |
|---|---:|---|
| AI API usage | **A$5–A$40 per job** | Depends on document length, number of pages, OCR quality, drawing analysis, model choice, and number of rechecks. Commercial API pricing is token-based and varies by provider.[1] [2] [3] |
| Cloud hosting and storage | **A$100–A$600 per month** | Depends on document volume, retention policy, backups, and security requirements. |
| OCR/document processing | **A$0–A$300 per month** | May be included in app stack or use separate services depending on accuracy requirements. |
| Maintenance and support | **A$1,500–A$5,000 per month** | Bug fixes, prompt/rule updates, model changes, HubSpot changes, user support. |
| Rule library maintenance | **0.5–2 staff hours per week** | Needed to encode new recurring issues and update AUSMAR-specific rules. |

The AI cost per job should not be the blocker. Even if a complex contract pack costs A$20–A$40 in AI/document processing, that is minor compared with the cost of a contract reissue, client confidence loss, missed credit/debit, drafting rework, or construction variation caused by incorrect documents.

### 11.3 Staff requirements

| Role | Setup involvement | Ongoing involvement |
|---|---|---|
| Sales Manager / process owner | Defines workflow, severity rules, sign-off expectations, HubSpot requirements. | Reviews metrics, enforces adoption, approves process changes. |
| Estimating representative | Confirms pricing structure, VO matching, credits/debits, standard pricing terminology. | Resolves pricing/spec discrepancies and updates rules. |
| Drafting representative | Confirms drawing review checklist, elevation rules, red pen interpretation, construction intent. | Resolves drawing/elevation issues and improves Pass 2 checklist. |
| Contracts admin | Confirms contract pack timing, file naming, issue process, client issue controls. | Uploads or triggers QA, monitors status. |
| Developer/vendor | Builds app, integrations, parsing, AI pipeline, reporting, security. | Maintains tool, fixes bugs, updates integrations. |
| QA reviewer | Tests historical jobs and validates tool findings. | Completes Pass 2 and verifies corrections. |

### 11.4 Training requirements

Training should be practical and short. Staff do not need AI theory. They need to know what documents to upload, how to read the discrepancy report, how to clear issues, and what blocks client issue.

| Audience | Training length | Content |
|---|---:|---|
| Sales consultants | 45–60 minutes | When QA runs, how to read findings, how to chase corrections, how to explain issues internally. |
| Contracts admin | 60–90 minutes | Intake requirements, HubSpot status, document versions, final sign-off. |
| Estimating | 60 minutes | Pricing/spec issue format, credit traceability, how to respond and close issues. |
| Drafting | 60–90 minutes | Pass 2 issue format, screenshots, elevations, fixture positioning, drawing corrections. |
| Managers | 30–45 minutes | Severity rules, exception approvals, dashboard metrics, enforcement. |

## 12. Success metrics

The tool should be measured by whether it prevents incorrect contract packs from reaching clients, reduces rework, and improves confidence between sales, estimating, drafting, and contracts admin.

| Metric | How to measure | Target after stabilisation |
|---|---|---:|
| Material error catch rate | Known material discrepancies caught before client issue divided by total known discrepancies. | **80–90%+** for pricing/spec issues; increasing trend for drawing issues. |
| Critical/high issues reaching client | Count of Critical/High contract QA issues found after client issue. | Reduce materially quarter by quarter; target near zero for repeat issue types. |
| Contract rework reduction | Number of contract packs reissued due to internal errors. | 30–50% reduction after adoption. |
| Pricing discrepancy value caught | Dollar value of missing/incorrect debits and credits identified before issue. | Track monthly and by category. |
| QA turnaround time | Time from contract pack ready to QA complete. | MVP target: same day or next business day depending on Pass 2 capacity. |
| False-positive rate | Flagged issues closed as “not an issue” divided by total flagged issues. | Keep low enough that staff trust the tool; refine rules monthly. |
| Repeat issue reduction | Frequency of same issue category over time. | Declining trend after feedback loop to estimating/drafting. |
| Adoption rate | Percentage of eligible contract packs run through QA before issue. | 95–100% once process is mandatory. |
| Exception rate | Number of High/Critical issues approved without correction. | Low and manager-visible. |

For S26JRGB2, success would mean the tool identifies the pricing/spec discrepancies in Pass 1, forces a structured drawing review in Pass 2, assigns actions to estimating and drafting, blocks client issue until resolved, and stores the final decision trail in HubSpot. The business benefit is not “AI found mistakes.” The business benefit is that AUSMAR stops preventable contract errors before they reach the client.

## 13. Why the two-pass model will work

The two-pass model works because it matches the actual nature of the risk. Contract errors are not all the same type of error. Some errors are commercial and text-based. Others are visual, positional, dimensional, or construction-intent based.

| Error type | Best detection method | Why |
|---|---|---|
| Missing credit/debit | Automated comparison | The signed amount can be matched to contract pricing and reconciled mathematically. |
| Deleted item still in specification | Automated comparison | Text extraction and clause comparison can identify the item still present. |
| Contradiction between pricing and spec | Automated comparison | The system can compare item status across documents. |
| Later VO reintroduced incorrectly | Automated comparison plus rule hierarchy | The system can apply signed document precedence. |
| Window/door head height mismatch | Human drawing review with AI assistance | Requires visual construction understanding and elevation interpretation. |
| Pendant versus wall light at specific height | Human drawing review with electrical checklist | The issue is not just quantity; it is fixture type, label, location, and mounting height. |
| Appliance clearance issue | Human drawing review | Requires practical buildability judgement and appliance/space understanding. |
| Mixer positioning error | Human drawing review | Requires wet-area layout interpretation, not just text matching. |

S26JRGB2 proves that Pass 1 alone would have been useful but incomplete. It would have caught several commercial and document logic defects, but it would have missed material drawing and construction-intent issues. Pass 2 alone would also be incomplete because humans may miss pricing reconciliation, untraceable credits, and specification contradictions across long documents. The correct design is therefore **AI-led comparison plus human-guided drawing review**, with both outputs combined into one enforceable sign-off workflow.

## 14. Developer build requirements

A developer should build the system around structured data, not just document chat. The following requirements should be treated as baseline requirements for the MVP production tool.

| Requirement ID | Requirement | Priority |
|---|---|---:|
| R-001 | The system must support upload of signed PSE/NHP, signed VOs, signed red pen markup, contract specification, contract pricing, and contract working drawings. | Must have |
| R-002 | The system must reject or park a QA request if mandatory documents are missing. | Must have |
| R-003 | The system must extract a VO register from signed NHP changes documents. | Must have |
| R-004 | The system must extract pricing lines from contract pricing documents. | Must have |
| R-005 | The system must compare every signed VO against contract pricing and report matched, unmatched, partially matched, superseded, and unclear items. | Must have |
| R-006 | The system must compare signed additions/deletions against contract specification wording. | Must have |
| R-007 | The system must flag contradictions between pricing, specification, and drawings where detectable. | Must have |
| R-008 | The system must apply document precedence rules, including later signed VO override logic. | Must have |
| R-009 | The system must produce issue records with severity, category, signed source, contract output, discrepancy, required action, owner, status, and evidence. | Must have |
| R-010 | The system must support Pass 2 human drawing review with category checklists and page/screenshot evidence. | Must have |
| R-011 | The system must generate a consolidated discrepancy report. | Must have |
| R-012 | The system must support issue status changes: open, in review, fixed, accepted exception, not applicable. | Must have |
| R-013 | The system must store an audit trail of uploads, AI runs, issue edits, resolutions, exceptions, and sign-off. | Must have |
| R-014 | The system should integrate with HubSpot deal stages, properties, tasks, notes, and report links. | Should have for MVP; must have for full system |
| R-015 | The system should maintain a versioned AUSMAR rules database. | Must have |
| R-016 | The system should allow recheck against corrected contract documents without losing the original issue history. | Must have |
| R-017 | The system should support analytics by category, owner, severity, and recurring issue type. | Later |

## 15. Governance and operating rules

The Contract QA Tool should be owned by the business, not by the AI model or the developer. AUSMAR must define the rules, thresholds, sign-off authority, and exception process.

| Governance item | Required decision |
|---|---|
| Mandatory use | Decide which deal types must pass Contract QA before client issue. Recommended answer: all NHP-to-contract jobs, especially those with signed VOs/red pen changes. |
| Blocking rules | Decide what blocks client issue. Recommended answer: all Critical issues and unresolved High issues unless manager-approved exception. |
| Dollar thresholds | Decide when pricing discrepancies become High or Critical. Recommended answer: any unexplained signed credit/debit mismatch is High; large or repeated discrepancy is Critical. |
| Exception authority | Decide who can approve issue without correction. Recommended answer: Sales Manager or Operations/GM-level approval for High/Critical exceptions. |
| Rules maintenance | Decide who can update AUSMAR rules. Recommended answer: process owner approves, developer implements, monthly review. |
| Data retention | Decide how long source documents, extracted data, reports, and audit logs are retained. Recommended answer: align with contract/legal document retention expectations. |
| Client communication | Decide whether any QA findings are ever client-facing. Recommended answer: internal only unless sales chooses to explain a corrected item. |

## 16. Practical go-live recommendation

AUSMAR should start with a focused MVP using S26JRGB2 as the benchmark job. The first release should not try to fully automate drawing intelligence. It should automate what AI is already good at: VO reconciliation, pricing/credit checks, spec wording checks, contradiction detection, metadata consistency, and structured issue reporting. It should then force a guided Pass 2 drawing/elevation review so the human reviewer catches construction-intent issues before client issue.

The correct first build target is:

| Build target | Recommendation |
|---|---|
| First benchmark job | S26JRGB2. |
| First MVP capability | Automated Pass 1 plus guided Pass 2 checklist. |
| First integration | Manual upload and report generation, then basic HubSpot status/report link. |
| First success test | Can the tool reproduce the known S26JRGB2 issues from both AI Pass 1 and human Pass 2? |
| First business rule | No contract pack goes to the client with open Critical issues or unexplained High issues. |

This approach gives AUSMAR the biggest risk reduction fastest. It does not pretend AI can fully replace experienced human drawing review. It uses AI where AI is strong and forces human review where human judgement is still required.

## 17. MVP acceptance criteria

The MVP should not be accepted simply because it generates a report. It should be accepted only if it proves that it can find real contract-stage issues, route them to the right person, and prevent unresolved material issues from being ignored.

| Acceptance criterion | Required test |
|---|---|
| S26JRGB2 benchmark accuracy | The MVP must reproduce the known S26JRGB2 Pass 1 findings for pricing, credits, specification contradictions, reintroduced deletions, and electrical quantity concerns. |
| Pass 2 workflow effectiveness | The MVP must guide a reviewer to record the known S26JRGB2 drawing issues, including elevation height, wall light labelling, laundry clearance, shelf note, mixer positions, LED placement, provision/install distinction, and powerpoint type. |
| Evidence quality | Every issue must include source document reference, contract document reference, discrepancy statement, and required action. |
| Blocking logic | The system must prevent a QA record being marked “Approved for client issue” while Critical issues remain open. |
| Recheck capability | Corrected contract documents must be uploadable and checked without deleting the original issue trail. |
| User usability | A trained sales/contracts user must be able to submit a QA request and read the report without developer help. |
| Owner routing | Pricing/spec issues must route to estimating; drawing/elevation/electrical layout issues must route to drafting or the nominated reviewer. |
| Audit trail | The system must show who uploaded documents, who reviewed issues, who changed statuses, and who approved exceptions. |

## 18. Key risks and controls

The main risk is not that the tool misses every error. The main risk is that AUSMAR treats an automated report as a complete review and removes the human judgement needed for drawings. The design must therefore make Pass 2 mandatory for changed jobs and clearly state that AI output is decision support, not final approval.

| Risk | Why it matters | Control |
|---|---|---|
| AI misses drawing-level detail | S26JRGB2 proves this can happen. | Mandatory Pass 2 for NHP jobs with red pen, VOs, electrical changes, wet-area changes, or external/elevation changes. |
| False positives reduce trust | Staff will ignore the tool if it flags too many non-issues. | Monthly review of false positives and adjustment of rules/prompts. |
| Wrong document version uploaded | QA may pass an outdated or incomplete pack. | Intake validation, version timestamps, checksum storage, and HubSpot document link control. |
| Signed source documents incomplete | The system cannot know what should have carried through. | Park QA until mandatory source documents are uploaded. |
| Rules become outdated | AUSMAR inclusions, terminology, and processes change. | Versioned rules database with nominated owner and monthly update cycle. |
| Staff bypass the process | Contract errors still reach clients. | HubSpot stage gate that requires QA status before client issue. |
| Sensitive client documents mishandled | Contract packs contain personal and commercial information. | Secure storage, role permissions, audit logs, and commercial API configuration with appropriate data handling settings. |
| Exception approvals become normal | High-risk errors may be waved through. | Exception dashboard and manager approval required for High/Critical items. |

## References

[1]: https://openai.com/api/pricing/ "OpenAI API Pricing"  
[2]: https://platform.claude.com/docs/en/about-claude/pricing "Anthropic Claude API Pricing"  
[3]: https://ai.google.dev/gemini-api/docs/pricing "Google Gemini Developer API Pricing"
