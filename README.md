# Email Invoice Extractor

Finance and operations teams in Latin America receive hundreds of invoices by email every month: PDFs, XMLs, sometimes both in the same thread. Opening each one to download the attachment takes time that adds up fast. This tool connects to your email server, finds every message with a matching attachment type, and batch-downloads them to a local folder.

---

## What it does

- Connects to any IMAP-compatible email server (Gmail, Outlook, Yahoo, custom)
- Scans your inbox for emails containing PDF and/or XML attachments
- Filters by attachment type: pull only PDFs, only XMLs, or both
- Groups matched attachments and downloads them in bulk to a local directory
- Skips duplicates across runs

---

## Why it exists

Mexican and Colombian tax compliance requires companies to collect and archive XML invoices (CFDIs) issued by every vendor. Most arrive by email. The standard workflow (open email, click attachment, save file, repeat) works at low volume. At 50+ invoices a month, someone is spending an hour doing nothing but saving files. This tool runs that process in a single CLI command.

---

## Stack

- **Language:** Python
- **Email protocol:** IMAP (works with any IMAP-enabled server)
- **Attachment parsing:** `imaplib`, `email`, `lxml`
- **CLI:** `argparse`

---

## Getting started

```bash
git clone https://github.com/emiliomt/Email-Invoice-Extractor.git
cd Email-Invoice-Extractor
pip install -r requirements.txt
cp .env.example .env  # Add your email credentials
```

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```
IMAP_SERVER=imap.gmail.com
EMAIL_ADDRESS=you@yourdomain.com
EMAIL_PASSWORD=your_app_password
OUTPUT_DIR=./downloads
```

For Gmail, use an App Password rather than your account password. For Outlook, enable IMAP access under account settings first.

---

## Usage

```bash
# Download all PDF and XML attachments
python extractor.py --type all

# Download PDFs only
python extractor.py --type pdf

# Download XMLs only
python extractor.py --type xml

# Filter by date range
python extractor.py --type all --since 2024-01-01 --until 2024-12-31

# Save to a custom directory
python extractor.py --type xml --output ./invoices/q1
```

---

## Example output

```
Scanning inbox...
Found 143 emails with matching attachments.

Downloading...
[██████████████████████] 143/143

Saved to: ./downloads
  PDFs:  89
  XMLs:  54
  Skipped (duplicates): 12
```

---

## Roadmap

- [ ] Filter by sender domain (e.g. pull only from verified vendors)
- [ ] Auto-rename files using invoice metadata (RFC, folio, date)
- [ ] Google Drive / Dropbox export
- [ ] SAT validation for Mexican CFDI XMLs

---

## Built by

Emilio Montemayor — [github.com/emiliomt](https://github.com/emiliomt)  
Chicago Booth MBA '26 | Building AI-native tools for back-office automation in Latin America
