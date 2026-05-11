# SOFI ETL

## Prerequisites

- Python 3.10+
- [Google Cloud SDK (gcloud)](https://cloud.google.com/sdk/docs/install)

**Install gcloud:**
- **Linux:** `curl https://sdk.cloud.google.com | bash && exec -l $SHELL`
- **Mac:** `brew install --cask google-cloud-sdk`
- **Windows:** Download and run the installer from the link above

---

## Setup

**1. Install dependencies:**
```bash
pip install -r requirements.txt
```

**2. Copy env file and fill in values:**
```bash
cp .env.example .env
```

---

## Authentication

### Google

Get `credentials.json` from Marc, then run:

**Linux/Mac:**
```bash
gcloud auth application-default login --client-id-file=credentials.json --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/spreadsheets"
```

**Windows (PowerShell):**
```powershell
gcloud auth application-default login --client-id-file=credentials.json --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/drive.readonly,https://www.googleapis.com/auth/spreadsheets"
```

Log in with your `@sofitech.ai` account when the browser opens. Delete `credentials.json` after authenticating.

---

### SSH Key

Generate your own SSH key pair **without a passphrase** (required — leave passphrase empty when prompted):

**Linux/Mac:**
```bash
ssh-keygen -t ed25519 -C "sofi-etl" -f ~/.ssh/sofi_etl
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.ssh"
ssh-keygen -t ed25519 -C "sofi-etl" -f "$env:USERPROFILE\.ssh\sofi_etl"
```

> If your key already has a passphrase, remove it with:
> ```bash
> ssh-keygen -p -f ~/.ssh/sofi_etl
> ```
> Enter your current passphrase, then press Enter twice to set no passphrase.

Send your **public key** to Marc:

**Linux/Mac:**
```bash
cat ~/.ssh/sofi_etl.pub
```

**Windows (PowerShell):**
```powershell
Get-Content "$env:USERPROFILE\.ssh\sofi_etl.pub"
```

Copy the output and send it to the admin. Never share your private key.

Set `HOSTINGER_SSH_KEY` in your `.env` to your private key path:
- **Linux/Mac:** `~/.ssh/sofi_etl`
- **Windows:** `C:\Users\YourName\.ssh\sofi_etl`

Then add the server to your known hosts (run once). **On Windows, use Python instead of PowerShell** to avoid encoding issues:

**Linux/Mac:**
```bash
ssh-keyscan -p 65002 145.223.109.254 >> ~/.ssh/known_hosts
```

**Windows (PowerShell — run via Python to avoid UTF-16 encoding issues):**
```powershell
python -c "
import subprocess, os
path = os.path.expanduser('~/.ssh/known_hosts')
result = subprocess.run(['ssh-keyscan', '-p', '65002', '145.223.109.254'], capture_output=True, text=True)
keys = [l for l in result.stdout.splitlines() if l.strip() and not l.startswith('#')]
with open(path, 'w', encoding='utf-8', newline='\n') as f:
    f.write('\n'.join(keys) + '\n')
"
```

> **Note:** Using `>>` in PowerShell writes UTF-16, which paramiko cannot read. Always use the Python snippet above on Windows.

---

### API Key

Get the API key from Marc. Include it in every request as a header:
```
X-API-Key: YOUR_API_KEY
```

---

## Run

```bash
python main.py
```

API available at `http://localhost:8000`

---

## Usage

**POST** `/etl/`

```bash
curl -X POST "http://localhost:8000/etl/?folder_id=FOLDER_ID&sheet_id=SHEET_ID&sheet_name=Sheet1&filter_type=both" \
  -H "X-API-Key: YOUR_API_KEY"
```
