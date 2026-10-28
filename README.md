# NeotecAI Intake for ERPNext (LocalAI)

Upload a supplier invoice or receipt (PDF, photo, scan) and get a **draft** ERPNext document back:

| Document | Becomes | Payment |
|---|---|---|
| Supplier tax invoice (credit) | Purchase Invoice (draft) | Draft Payment Entry is created automatically when the PI is submitted |
| Receipt already paid by the company (fuel, supplies, cash/mada) | Purchase Invoice marked **paid** (`is_paid`, Mode of Payment) | Recorded on the invoice itself |
| Receipt paid personally by an employee | Expense Claim (draft, HRMS) | Draft employee Payment Entry once the claim is approved and submitted |

All AI work runs on **your own LocalAI server**. Documents never leave your network.

Nothing is submitted automatically. A person always reviews and submits.

## How it works

```
Upload (desk, bulk upload, or email inbox)
   │
   ▼
NeotecAI Document Intake  ──background job──►  LocalAI vision model (JSON-schema constrained)
   │                                     + ZATCA QR decoder (seller VAT, totals, date)
   ▼
Review screen: supplier, VAT no., invoice no./date, lines, totals, warnings, document preview
   │
   ▼  [Create Purchase Invoice] / [Create Expense Claim]
Draft PI / Expense Claim (file attached)  ──submit──►  draft Payment Entry (optional)
```

### Checks done for every document (Saudi VAT aware)
- Reads the **ZATCA QR code** (Phase 1 and Phase 2 TLV). The QR is treated as the authoritative source for the seller VAT number, total and VAT total.
- Validates the Saudi VAT number format: 15 digits, starting and ending with 3.
- Input VAT is posted to your Input VAT account **only** when the document has a valid supplier VAT number. Otherwise VAT is added to the expense cost and a warning is shown. The reviewer can override the "Input VAT Recoverable" checkbox.
- Checks that net + VAT = total, that the lines add up to the net total, and that VAT ≈ 15%.
- Detects duplicates by supplier + invoice no. and by identical file. A duplicate Purchase Invoice is blocked.
- Converts Arabic-Indic digits and currency spellings (SR, ر.س). The model is told to convert Hijri-only dates to Gregorian; check those.
- Flags invoice dates in the future or more than a year old.
- Matches the supplier by VAT number, then by exact name, then by similar name (flagged for confirmation). Optionally creates new suppliers automatically.
- Maps each line to an expense **category**. Each category points to an Item (for the Purchase Invoice; its Item Defaults hold the expense account) and an Expense Claim Type.

## Requirements
- Frappe / ERPNext **v15** (tested on v15.x; v16 not yet tested)
- HRMS app, for Expense Claims only
- A LocalAI server with a vision model (see Hardware below)
- Python packages, installed automatically: `pypdfium2` (PDF rendering) and `zxing-cpp` (QR reading). Optional: `pillow-heif` for iPhone HEIC photos.

## Install

```bash
cd ~/frappe-bench
bench get-app /path/to/neotecai_intake        # or your git URL
bench --site your.site install-app neotecai_intake
bench build --app neotecai_intake
bench restart
```

Make sure a worker for the **long** queue is running (it is in standard production setups).

## Set up LocalAI (on the AI server)

```bash
# NVIDIA GPU server
docker run -d --name localai --restart unless-stopped --gpus all -p 8080:8080 \
  -e LOCALAI_API_KEY=change-me-long-random-key \
  -v /srv/localai/models:/models \
  localai/localai:latest-gpu-nvidia-cuda-12

# install the vision model from the gallery (pick per the hardware table)
docker exec -it localai local-ai models install qwen3.6-35b-a3b
```

- In `/srv/localai/models/<model>.yaml`, set `context_size: 16384` so three invoice pages fit.
- Firewall port 8080 so that **only the Frappe server** can reach it.

## Configure (NeotecAI Intake Settings)
1. **LocalAI Server**: Base URL (e.g. `http://10.0.0.20:8080`), API Key, Vision Model (same name as in LocalAI). Click **Test Connection**.
2. **Company Accounts**: one row per company with the Input VAT account (e.g. *VAT 15% Input*), Cost Center and default Mode of Payment. The Mode of Payment needs its account set for the company.
3. **Expense Categories**: 11 defaults are installed. For each, set:
   - **Item**: a non-stock purchase item whose *Item Defaults → Expense Account* is the right GL account (e.g. item `FUEL` → *Fuel Expenses*).
   - **Expense Claim Type**: e.g. *Food*, *Travel*. Each type needs its account set for the company.
   - Set a **Fallback Item** for lines without a category item.
4. Behaviour options: extract on upload, posting date = invoice date, auto-create suppliers.
5. Optional **Email inbox**: choose an Email Account (e.g. `invoices@yourco.com`). PDF and image attachments received there become intake documents automatically.

## Daily use
- **NeotecAI Document Intake → Upload Invoices / Receipts**: select many files at once, choose the company, and choose *Purchase Invoice* or *Expense Claim* (+ employee).
- Or create one manually: attach the file, choose *Create As* and *Payment*, then save. Extraction starts automatically (about 10–60 s per document, depending on hardware).
- Open the document. The preview is on top and the extracted fields below. Fix anything flagged in orange, then click **Create Purchase Invoice / Create Expense Claim**.
- Review and submit the draft as usual. Payment drafts appear as configured. **Create → Create Payment** is also available manually.
- Employees (role *Employee*) can upload their own receipts. Their Employee record is filled in automatically.

Status flow: `Draft → Queued → Processing → Needs Review / Ready → Created → Completed` (or `Failed` / `Cancelled`). Deleting a draft PI or Expense Claim frees the intake so you can create it again.

## Permissions
- *Accounts Manager / System Manager*: everything, including settings.
- *Accounts User*: upload, review, create documents.
- *Employee*: own uploads only. Creating an Expense Claim follows normal HRMS permissions.
- Created documents are inserted with the **user's own permissions**. The AI never submits, cancels or pays anything.

---

## Hardware guide

The workload is one vision-language model reading 1–3 page images per document and writing about 1,000 tokens of JSON. GPU memory (VRAM) decides which model fits; the model decides accuracy on poor photos and Arabic text.

| Tier | Volume | Hardware | Model (LocalAI gallery name) | Approx. time per document* |
|---|---|---|---|---|
| **Pilot, CPU only** | < 50 / day | 16+ core modern CPU (EPYC / Xeon / Ryzen 9), 64 GB RAM, 1 TB NVMe | `qwen3.5-9b` or `gemma-4-12b-it-qat-q4_0` | 1–4 min (runs in the background queue) |
| **Recommended start** | up to ~1,000 / day | 1× **24 GB** GPU (RTX 4090 / NVIDIA L4), 8–16 cores, 64 GB RAM, 2 TB NVMe | `gemma-4-26b-a4b-it-qat-q4_0` (≈15 GB) or `qwen3.6-27b` | 10–30 s |
| **Best value accuracy** | ~1,000–3,000 / day | 1× **32 GB** GPU (RTX 5090), 16 cores, 96–128 GB RAM, 2 TB NVMe | `qwen3.6-35b-a3b` (≈22 GB, fast MoE) | 8–20 s |
| **Department / multi-use** | 3,000+ / day, plus chat or RAG later | 1× **48–96 GB** GPU (RTX 6000 Ada 48 GB, L40S 48 GB, RTX PRO 6000 96 GB), 32 cores, 128–256 GB RAM, 4 TB NVMe | `qwen3.6-35b-a3b` at Q8 with parallel requests, + `bge-m3` embeddings | 5–15 s, several at once |
| **Quiet office box** | up to ~500 / day | Mac Studio, M-series Max/Ultra, 64–128 GB unified memory | `qwen3.6-35b-a3b` (Metal image) | 15–40 s |

\*These are planning estimates, not measurements. Run a pilot of 50–100 of your own invoices and time them on the real box before you scale.

**Server details (GPU tiers)**
- OS: Ubuntu 24.04 LTS, NVIDIA driver plus NVIDIA Container Toolkit, Docker.
- PSU: 1000 W+ for an RTX 4090/5090 workstation. For a rack server, use a datacenter card (L4, L40S, RTX PRO 6000 Server) for proper airflow.
- Disk: each model is 15–60 GB, so keep 500 GB+ free for models and versions.
- Network: 1 GbE LAN to the Frappe server is plenty. Put the box on a UPS.
- Keep LocalAI on its own machine (not on the ERPNext server) so model load never slows ERPNext.
- On-premises hosting keeps invoice data inside your company and the Kingdom, which helps with PDPL / data-residency requirements.

**Choosing the model**: start with the largest model your GPU fits from the table. If accuracy on blurry phone photos or Arabic-only receipts is not good enough, move up one tier rather than tuning prompts. The QR check catches most total/VAT errors automatically on ZATCA e-invoices.

## Limitations
- Expense Claims are in company currency. For foreign-currency receipts, use a Purchase Invoice.
- Handwritten receipts and very long invoices (more than *Max PDF Pages*) need more review.
- The desk UI (buttons, preview, bulk upload) is standard Frappe form/list JS.

## Testing
`tests/e2e.py` runs (with `bench execute` after copying it into the app) the full flow against a mock LocalAI: tax invoice PDF with QR → PI → submit → Payment Entry; receipt photo → Expense Claim → approve → employee payment; paid fuel receipt without a VAT number → paid PI; error handling; duplicates.
