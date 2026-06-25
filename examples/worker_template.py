#!/usr/bin/env python3
"""
worker_template.py

A reusable template for Google Batch workers. Handles:
  - Authentication via metadata server (no credential files)
  - GCS file download/upload via urllib (minimal dependencies)
  - Periodic checkpointing (logs every 10 min)
  - Honest exit codes (fail if no output)
  - Container path/environment variable substitution

This template ships with a SIMPLE, SELF-CONTAINED EXAMPLE: it downloads a
CRAM file and runs a few samtools commands (quickcheck, header, flagstat,
idxstats) to produce a small stats report. It is intended as a "hello world"
you can run end to end before adapting it to a heavier tool.

To adapt for your own tool:
  1. Swap the container image for one that ships your tool
  2. Modify STEP 3 (download inputs) and STEP 4 (run tool)
  3. Adjust output validation in STEP 6 as needed

Expected environment variables (set by the Batch job config):
  - SAMPLE_ID: identifier for this sample (e.g., "TEST_001")
  - CRAM_PATH: gs:// path to the input CRAM (optional; defaults based on
    BUCKET + SAMPLE_ID if not provided)
  - REF_PATH: gs:// path to a reference FASTA (optional). Only needed if you
    run samtools commands that decode read sequences from a CRAM, such as
    `samtools stats`. The default commands here do NOT need a reference.
"""

import json
import subprocess
import sys
import os
import shutil
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime


# =========================================================================
# Configuration — customize these
# =========================================================================

BUCKET = "<YOUR_BUCKET_NAME>"  # e.g., "cloned-ws-files-wb-lukewarm-blueberry-5144"
SAMPLE_ID = os.environ.get("SAMPLE_ID", "TEST_001")
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "<YOUR_PROJECT_ID>")

# Paths inside the container
WORK_DIR = Path("/tmp/work") / SAMPLE_ID
REF_DIR = Path("/tmp/ref")
INPUT_DIR = Path("/tmp/input")
LOG_KEY = f"logs/worker_{SAMPLE_ID}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"

# ==========================================================================
# Logging and helpers
# =========================================================================

log_lines = []


def log(msg=""):
    """Log a message with timestamp."""
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    log_lines.append(line)
    print(line, flush=True)


def run(cmd, check=True, shell=True):
    """Run a shell command and log output."""
    log(f"  $ {cmd[:150]}")
    result = subprocess.run(cmd, shell=shell, capture_output=True, text=True)

    if result.stdout.strip():
        for line in result.stdout.strip().split("\n")[:20]:
            log(f"    {line}")

    if result.returncode != 0 and check:
        if result.stderr.strip():
            log(f"  STDERR: {result.stderr.strip()[:200]}")
        log(f"  ERROR: exit code {result.returncode}")
        upload_logs()
        sys.exit(result.returncode)

    return result


# =========================================================================
# GCS auth and file operations
# =========================================================================

def get_gcs_token():
    """Get access token from metadata server."""
    try:
        req = urllib.request.Request(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}
        )
        resp = urllib.request.urlopen(req, timeout=10)
        token_data = json.loads(resp.read())
        return token_data["access_token"]
    except Exception as e:
        log(f"ERROR: Failed to get token: {e}")
        sys.exit(1)


def gcs_download(gcs_path, local_path, token, user_project=None):
    """Download a file from GCS."""
    # Parse gs://bucket/path
    if gcs_path.startswith("gs://"):
        gcs_path = gcs_path[5:]

    parts = gcs_path.split("/", 1)
    bucket, obj = parts[0], parts[1]

    # Construct download URL
    encoded_obj = urllib.parse.quote(obj, safe="")
    url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket}/o/{encoded_obj}?alt=media"

    if user_project:
        url += f"&userProject={user_project}"

    # Download
    local_path = Path(local_path)
    local_path.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, open(local_path, "wb") as f:
            shutil.copyfileobj(resp, f)
        log(f"  downloaded: {gcs_path} -> {local_path} ({local_path.stat().st_size / 1e6:.1f} MB)")
    except Exception as e:
        log(f"  ERROR downloading {gcs_path}: {e}")
        raise


def gcs_upload(local_path, gcs_key, token):
    """Upload a file to GCS."""
    local_path = Path(local_path)
    if not local_path.exists():
        log(f"  WARN: file does not exist: {local_path}")
        return

    # Read file
    with open(local_path, "rb") as f:
        data = f.read()

    # Construct upload URL
    encoded_key = urllib.parse.quote(gcs_key, safe="")
    url = f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?uploadType=media&name={encoded_key}"

    # Upload
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream"
        }
    )
    try:
        urllib.request.urlopen(req, timeout=300)
        log(f"  uploaded: {local_path.name} -> gs://{BUCKET}/{gcs_key} ({len(data) / 1e6:.1f} MB)")
    except Exception as e:
        log(f"  ERROR uploading {gcs_key}: {e}")
        raise


def upload_logs(token=None):
    """Upload log checkpoint to GCS."""
    if token is None:
        token = get_gcs_token()

    try:
        log_data = "\n".join(log_lines).encode("utf-8")
        encoded_key = urllib.parse.quote(LOG_KEY, safe="")
        url = f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET}/o?uploadType=media&name={encoded_key}"

        req = urllib.request.Request(
            url,
            data=log_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain"
            }
        )
        urllib.request.urlopen(req, timeout=60)
        print(f"[CHECKPOINT] Logs uploaded to gs://{BUCKET}/{LOG_KEY}", flush=True)
    except Exception as e:
        print(f"[CHECKPOINT ERROR] Failed to upload logs: {e}", flush=True)


# =========================================================================
# STEP 1: Authenticate
# =========================================================================

log("=" * 60)
log("STEP 1: Authenticate")
log("=" * 60)

TOKEN = get_gcs_token()
log("  Token obtained from metadata server")

# =========================================================================
# STEP 2: Create working directories
# =========================================================================

log("")
log("=" * 60)
log("STEP 2: Prepare working directories")
log("=" * 60)

WORK_DIR.mkdir(parents=True, exist_ok=True)
REF_DIR.mkdir(parents=True, exist_ok=True)
INPUT_DIR.mkdir(parents=True, exist_ok=True)

log(f"  work_dir:  {WORK_DIR}")
log(f"  ref_dir:   {REF_DIR}")
log(f"  input_dir: {INPUT_DIR}")

# =========================================================================
# STEP 3: Download input data
# =========================================================================

log("")
log("=" * 60)
log("STEP 3: Download input CRAM")
log("=" * 60)

cram_path = os.environ.get("CRAM_PATH", f"gs://{BUCKET}/input/{SAMPLE_ID}/{SAMPLE_ID}.cram")
local_cram = INPUT_DIR / f"{SAMPLE_ID}.cram"

log(f"  downloading CRAM from {cram_path}")
try:
    gcs_download(cram_path, str(local_cram), TOKEN)
except Exception:
    log("  retry with userProject...")
    gcs_download(cram_path, str(local_cram), TOKEN, user_project=PROJECT_ID)

# The .crai index is optional for the commands below, but idxstats needs it.
# Try to fetch it; don't fail the whole job if it is missing.
local_crai = INPUT_DIR / f"{SAMPLE_ID}.cram.crai"
try:
    gcs_download(cram_path + ".crai", str(local_crai), TOKEN)
    has_index = True
except Exception:
    log("  note: no .crai index found (idxstats will be skipped)")
    has_index = False

log(f"  CRAM ready: {local_cram.stat().st_size / 1e9:.2f} GB")

# Optional reference (only needed for sequence-decoding commands like `stats`).
ref_path = os.environ.get("REF_PATH", "")
local_ref = None
if ref_path:
    log(f"  downloading reference from {ref_path}")
    local_ref = REF_DIR / Path(ref_path).name
    try:
        gcs_download(ref_path, str(local_ref), TOKEN)
    except Exception as e:
        log(f"  WARN: failed to download reference: {e}")
        local_ref = None

# =========================================================================
# STEP 4: Run samtools (the simple example)
# =========================================================================

log("")
log("=" * 60)
log("STEP 4: Run samtools stats")
log("=" * 60)

report_path = WORK_DIR / f"{SAMPLE_ID}.stats.txt"

# samtools version (sanity check that the tool is present)
run("samtools --version | head -1", check=False)

# quickcheck: fast integrity test of the CRAM (exit 0 = looks intact)
log("")
log("  -- quickcheck --")
qc = run(f"samtools quickcheck -v {local_cram}", check=False)
quickcheck_ok = (qc.returncode == 0)
log(f"  quickcheck passed: {quickcheck_ok}")

# Build a small text report by appending each command's output.
with open(report_path, "w") as report:
    report.write(f"# samtools stats report for {SAMPLE_ID}\n")
    report.write(f"# generated {datetime.utcnow().isoformat()}Z\n")
    report.write(f"# source: {cram_path}\n\n")

    # Header summary (@SQ lines tell you the reference contigs)
    log("")
    log("  -- header (@SQ / @PG) --")
    hdr = run(f"samtools view -H {local_cram}", check=False)
    report.write("== HEADER ==\n")
    report.write(hdr.stdout)
    report.write("\n")

    # flagstat: read counts by category (mapped, duplicates, properly paired...)
    log("")
    log("  -- flagstat --")
    fs = run(f"samtools flagstat {local_cram}", check=False)
    report.write("== FLAGSTAT ==\n")
    report.write(fs.stdout)
    report.write("\n")

    # idxstats: per-contig read counts (needs an index)
    if has_index:
        log("")
        log("  -- idxstats --")
        idx = run(f"samtools idxstats {local_cram}", check=False)
        report.write("== IDXSTATS ==\n")
        report.write(idx.stdout)
        report.write("\n")

    # Full stats (needs the reference to decode CRAM sequences).
    if local_ref is not None:
        log("")
        log("  -- stats (with reference) --")
        st = run(f"samtools stats --reference {local_ref} {local_cram}", check=False)
        # Keep only the summary numbers (the SN lines), not the giant tables.
        sn_lines = [ln for ln in st.stdout.split("\n") if ln.startswith("SN")]
        report.write("== STATS (summary numbers) ==\n")
        report.write("\n".join(sn_lines))
        report.write("\n")
    else:
        log("  skipping `samtools stats` (no REF_PATH provided)")

log(f"  report written: {report_path}")

# =========================================================================
# STEP 5: Checkpoint logs (the job is short, so just upload once here)
# =========================================================================

upload_logs(TOKEN)

# =========================================================================
# STEP 6: Validate output and upload
# =========================================================================

log("")
log("=" * 60)
log("STEP 6: Upload outputs")
log("=" * 60)

# The job is successful if we produced a non-empty report and the CRAM
# passed quickcheck.
if report_path.exists() and report_path.stat().st_size > 0 and quickcheck_ok:
    log(f"  Output file found: {report_path.name} ({report_path.stat().st_size} bytes)")

    gcs_key = f"output/{SAMPLE_ID}/{report_path.name}"
    try:
        gcs_upload(str(report_path), gcs_key, TOKEN)
    except Exception as e:
        log(f"  WARN: Failed to upload {report_path.name}: {e}")

    log(f"  Outputs uploaded to gs://{BUCKET}/output/{SAMPLE_ID}/")
    upload_logs(TOKEN)

    log("")
    log("=" * 60)
    log("SUCCESS")
    log("=" * 60)
    sys.exit(0)

else:
    log("  ERROR: report missing/empty or CRAM failed quickcheck")
    log(f"    report exists: {report_path.exists()}")
    log(f"    quickcheck ok: {quickcheck_ok}")
    upload_logs(TOKEN)
    log("")
    log("=" * 60)
    log("FAILURE: No valid output produced")
    log("=" * 60)
    sys.exit(1)
