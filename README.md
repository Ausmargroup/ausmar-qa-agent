# AUSMAR QA Agent

Automated QA across the AUSMAR sales-to-contract pipeline. What began as the PSE deposit QA tool (Stage 1) now runs three sequential stages, all in one app, sharing one database, one rule library, and one self-learning feedback system. **Stage 1 is unchanged** — Stages 2 and 3 are additive.

## The Three Stages

| Stage | Trigger | Checks | Inputs | Verdicts |
|-------|---------|--------|--------|----------|
| **Stage 1 — PSE / Deposit QA** | At deposit | Original PSE submission QA (unchanged) | Deal-code `.zip` | ACCEPTED / NOT ACCEPTED |
| **Stage 2 — NHP Review** | After estimating produces the NHP | Every signed VO carried into the Final NHP, amounts match, total reconciles | NHP Changes PDF + Final NHP PDF | PASS / REVIEW REQUIRED / FAIL |
| **Stage 3 — Pre-Contract QA** | Before issuing the contract | Two-pass check that the contract pack matches the signed source, itemised by spec section + drawing page | Signed source + contract spec + contract pricing + working drawings (+ optional VOs/red pen) | READY TO ISSUE / ISSUE WITH NOTED ITEMS / ISSUE AFTER CORRECTIONS / DO NOT ISSUE / PARKED |

### Stage 2 — NHP Review
The AI extracts the VO register and the Final NHP pricing lines, but **all dollar matching and total reconciliation are done deterministically in Python** so the verdict never depends on LLM arithmetic. Missing VOs are Critical (FAIL); amount mismatches are High (REVIEW REQUIRED). Base + net signed VO movement is reconciled against the Final NHP grand total; any gap is flagged with the exact dollar amount. If amounts or totals can't be read with confidence, the item is flagged for human review — never silently passed.

### Stage 3 — Pre-Contract QA
**Pass 1 (automatic):** compares the signed source against the contract spec and pricing text — VO carry-through, debit/credit matching, deleted items still listed, pricing-vs-spec contradictions, metadata. **Pass 2 (AI-assisted drawing review):** a vision model reads the working-drawing pages and points the reviewer to likely issues per sheet. Every Pass 2 finding is tagged **"Confirm on drawing"** — the tool locates the issue, a human confirms it. If any required document is absent, the job returns **PARKED** rather than a misleading pass.

## Self-Learning Rule Library (no developer required)

Every Stage 2/3 check comes from a rule stored in the database and editable entirely through the UI:

- **Rules tab** (admin code) — change a rule's severity, switch a rule on/off, add an **exclusion** (text telling a rule what it must *not* flag — injected straight into the AI prompt on the next review), or add a brand-new rule in plain English.
- **Learning tab** — lists every issue staff marked **False Positive** or **Not Applicable**, so admins can decide what to exclude or down-rank. This is how the tool improves over time with zero code changes.
- **Rule history** — every rule change is logged (who/what/when) as a permanent audit trail.
- Seeded with 28 rules (6 Stage 2 pricing/reconciliation rules, 22 Stage 3 spec/drawing/electrical/wet-area rules) extracted from AUSMAR documentation.

A built-in plain-English guide for non-technical staff is served at **`/docs`** (also linked as **Help** in the nav).

## What Stage 1 Does (unchanged)

- **Plan-to-Lot Fit Verification** — extracts lot dimensions from GeoSite PDFs via AI vision, compares against known plan minimum widths
- **GeoSite Verification** — checks authenticity, required elements (contours, setbacks, north arrow, scale)
- **Red Pen Markup Verification** — checks colour is red, on AUSMAR base plan, has dimensions
- **Document Completeness** — verifies all required files are present per official 1.0 PSE Document Naming
- **Naming Conventions** — enforces Title Case + (Signed) format, auto-fixes incorrect names
- **File Structure** — flat zip, no subfolders, no duplicates, no junk files, auto-fixes issues
- **PSE Content Analysis** — checks for common sales accept issues, gas ban estates, sites with fall
- **Auto-Fix** — corrects naming/structure issues and returns a corrected zip for download
- **Feedback/Learning** — staff can mark issues as correct or false positive to improve accuracy
- **Pre-Log** — upload deposit info before the zip arrives for cross-checking
- **Review History** — dashboard with stats and full detail for every past review

## Tech Stack

- Python 3.11+ / Flask / Gunicorn
- SQLite (file-based, persistent)
- OpenAI API (GPT-4.1-nano for text, GPT-4.1-mini for vision/PDF analysis)
- poppler-utils (PDF to image conversion)

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | Yes | OpenAI API key for document analysis |
| `OPENAI_BASE_URL` | No | Custom OpenAI base URL (defaults to `https://api.openai.com/v1`) |
| `PORT` | No | Server port (defaults to `5000`) |

---

## Deploy to Render (Recommended)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Ausmargroup/ausmar-qa-agent)

### Manual Render Deployment

1. Go to [render.com](https://render.com) and sign in (or create a free account)
2. Click **New +** > **Web Service**
3. Connect your GitHub account and select the `ausmar-qa-agent` repository
4. Configure:
   - **Name:** `ausmar-qa-agent`
   - **Runtime:** Docker
   - **Instance Type:** Starter ($7/mo) or Free (spins down after inactivity)
   - **Disk:** Add a disk — mount path `/app/data`, size 1 GB (required for SQLite persistence)
5. Under **Environment Variables**, add:
   - `OPENAI_API_KEY` = your OpenAI API key
6. Click **Deploy Web Service**
7. Wait 3-5 minutes for the build to complete
8. Your app will be live at `https://ausmar-qa-agent.onrender.com`

### Important: Persistent Storage

SQLite needs a persistent disk to survive redeployments. On Render:
- Go to your service > **Disks** > **Add Disk**
- Mount Path: `/app/data`
- Size: 1 GB (plenty for thousands of reviews)

---

## Deploy to Railway

1. Go to [railway.app](https://railway.app) and sign in
2. Click **New Project** > **Deploy from GitHub Repo**
3. Select the `ausmar-qa-agent` repository
4. Add environment variable: `OPENAI_API_KEY` = your key
5. Railway auto-detects the Dockerfile and deploys
6. Add a volume mounted to `/app/data` for SQLite persistence
7. Generate a domain under **Settings** > **Networking** > **Generate Domain**

---

## Deploy with Docker (Any VPS)

```bash
# Clone the repo
git clone https://github.com/Ausmargroup/ausmar-qa-agent.git
cd ausmar-qa-agent

# Create .env file
echo "OPENAI_API_KEY=sk-your-key-here" > .env

# Run with Docker Compose
docker compose up -d

# App is now running at http://your-server-ip:5000
```

---

## Deploy Without Docker (Any Linux Server)

```bash
# Clone the repo
git clone https://github.com/Ausmargroup/ausmar-qa-agent.git
cd ausmar-qa-agent

# Install system dependencies
sudo apt-get update && sudo apt-get install -y python3.11 python3-pip poppler-utils

# Install Python dependencies
pip install -r requirements.txt

# Set environment variable
export OPENAI_API_KEY="sk-your-key-here"

# Run with gunicorn (production)
gunicorn --bind 0.0.0.0:5000 --timeout 300 --workers 2 app:app

# Or run directly (development)
python3 app.py
```

To run as a system service, copy `ausmar-qa.service` to `/etc/systemd/system/` and update the `.env` file path, then:

```bash
sudo systemctl enable ausmar-qa
sudo systemctl start ausmar-qa
```

---

## Project Structure

```
ausmar-qa-agent/
├── app.py                  # Flask routes — Stage 1/2/3, rules admin, learning, /docs
├── qa_engine.py            # Stage 1 PSE QA pipeline with auto-fix (unchanged)
├── nhp_engine.py           # Stage 2 NHP review engine (VO reconciliation)
├── contract_qa_engine.py   # Stage 3 two-pass pre-contract QA engine
├── engine_common.py        # Shared PDF extraction + LLM helpers for Stage 2/3
├── database.py             # Stage 1 schema/helpers (Postgres + SQLite, unchanged)
├── db_v2.py                # Stage 2/3 + rule-library tables, helpers, seed data
├── templates/
│   ├── index.html          # Frontend (single-page app, all stages)
│   └── docs.html           # Plain-English how-to guide (served at /docs)
├── ARCHITECTURE_V2.md      # V2 technical specification
├── Dockerfile              # Docker build config
├── docker-compose.yml      # Docker Compose for self-hosting
├── requirements.txt        # Python dependencies
├── railway.json            # Railway config
├── render.yaml             # Render Blueprint config
├── extracted_rules.md      # Reference: QA rules extracted from AUSMAR docs
└── data/                   # SQLite database + uploads (created at runtime)
```

The V2 tables (`qa_rules`, `rule_exclusions`, `rule_history`, `contract_reviews`, `contract_issues`) are created idempotently on boot by `db_v2.init_v2()` in **both** the Postgres (Railway) and SQLite (local) backends. Existing Stage 1 tables in `database.py` are never altered.

## Cost

~2-5 cents per review depending on PDF page count. Uses GPT-4.1-nano for text analysis and GPT-4.1-mini for vision (PDF page analysis).

## Known Plans (Pre-Loaded)

| Plan | Min Width | Min Length | Area |
|------|-----------|------------|------|
| Clearwater 225 | 12.3m | 29.1m | 225.95 m² |
| Clearwater 245 | 13.0m | 29.2m | 245.51 m² |
| Narrabeen | 10.0m | 25.0m | 212.33 m² |

Staff can add more plans via the Plans tab in the app.
