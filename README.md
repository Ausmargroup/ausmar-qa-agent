# AUSMAR PSE QA Agent

Automated QA review system for AUSMAR PSE (Pre-Start Estimate) deposit submissions. Upload a zip file, get a detailed QA verdict back with auto-corrections applied.

## What It Does

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
├── app.py              # Flask routes and API endpoints
├── qa_engine.py        # Full QA review pipeline with auto-fix
├── database.py         # SQLite models and queries
├── templates/
│   └── index.html      # Frontend (single-page app)
├── Dockerfile          # Docker build config
├── docker-compose.yml  # Docker Compose for self-hosting
├── requirements.txt    # Python dependencies
├── Procfile            # For Heroku/Railway
├── railway.json        # Railway config
├── render.yaml         # Render Blueprint config
├── extracted_rules.md  # Reference: all QA rules extracted from AUSMAR docs
└── data/               # SQLite database (created at runtime)
```

## Cost

~2-5 cents per review depending on PDF page count. Uses GPT-4.1-nano for text analysis and GPT-4.1-mini for vision (PDF page analysis).

## Known Plans (Pre-Loaded)

| Plan | Min Width | Min Length | Area |
|------|-----------|------------|------|
| Clearwater 225 | 12.3m | 29.1m | 225.95 m² |
| Clearwater 245 | 13.0m | 29.2m | 245.51 m² |
| Narrabeen | 10.0m | 25.0m | 212.33 m² |

Staff can add more plans via the Plans tab in the app.
