#!/usr/bin/env python3
"""
worker_template.py

A reusable template for Google Batch workers. Handles:
  - Authentication via metadata server (no credential files)
  - GCS file download/upload via urllib (minimal dependencies)
  - Periodic checkpointing (logs every 10 min)
  - Honest exit codes (fail if no output)
  - Container path/environment variable substitution

To adapt for your tool:
  1. Import your tool's CLI/library instead of xTea
  2. Modify STEP 3 (download inputs) and STEP 4 (run tool)
  3. Adjust checkpointing interval and output validation as needed

Expected environment variables (set by Batch job config):
  - SAMPLE_ID: identifier for this sample (e.g., "SAMPLE_001")
  - TOOL_SPECIFIC_VAR: any tool-specific input path or parameter

"""

import json
import subprocess
import sys
import os
import shutil
import threading
import time
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

# Checkpointing
CHECKPOINT_INTERVAL = 600  # seconds (every 10 min)

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
        for line in result.stdout.strip().split("\n")[:10]:
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
        log(f"  downloaded: {gcs_path} → {local_path} ({local_path.stat().st_size / 1e6:.1f} MB)")
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
        log(f"  uploaded: {local_path.name} → gs://{BUCKET}/{gcs_key} ({len(data) / 1e6:.1f} MB)")
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
log("  ✓ Token obtained from metadata server")

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

for subdir in ["tmp", "logs"]:
    (WORK_DIR / subdir).mkdir(parents=True, exist_ok=True)

log(f"  work_dir: {WORK_DIR}")
log(f"  ref_dir:  {REF_DIR}")
log(f"  input_dir: {INPUT_DIR}")

# =========================================================================
# STEP 3: Download reference files (customize as needed)
# =========================================================================

log("")
log("=" * 60)
log("STEP 3: Download reference files")
log("=" * 60)

# Example: download reference genome index
ref_files = [
    "ref/Homo_sapiens_assembly38.fasta",
    "ref/Homo_sapiens_assembly38.fasta.fai",
    "ref/gencode.v45.annotation.gff3",
]

for obj in ref_files:
    local = REF_DIR / obj.split("/", 1)[1]  # remove "ref/" prefix
    if not local.exists():
        try:
            gcs_download(f"gs://{BUCKET}/{obj}", str(local), TOKEN)
        except Exception as e:
            log(f"  WARN: Failed to download {obj}: {e}")
    else:
        log(f"  skipped (exists): {local}")

log(f"  ✓ Reference files ready in {REF_DIR}")

# =========================================================================
# STEP 4: Download input data (customize as needed)
# =========================================================================

log("")
log("=" * 60)
log("STEP 4: Download input data")
log("=" * 60)

# Example: download a CRAM file
cram_path = os.environ.get("CRAM_PATH", f"gs://{BUCKET}/input/{SAMPLE_ID}/{SAMPLE_ID}.cram")
cram_index = cram_path + ".crai"

local_cram = INPUT_DIR / f"{SAMPLE_ID}.cram"
local_crai = INPUT_DIR / f"{SAMPLE_ID}.cram.crai"

log(f"  downloading CRAM from {cram_path}")
try:
    gcs_download(cram_path, str(local_cram), TOKEN)
except Exception as e:
    log(f"  retry with userProject...")
    gcs_download(cram_path, str(local_cram), TOKEN, user_project=PROJECT_ID)

log(f"  downloading CRAI from {cram_index}")
try:
    gcs_download(cram_index, str(local_crai), TOKEN)
except Exception as e:
    log(f"  retry with userProject...")
    gcs_download(cram_index, str(local_crai), TOKEN, user_project=PROJECT_ID)

log(f"  ✓ CRAM ready: {local_cram.stat().st_size / 1e9:.1f} GB")

# =========================================================================
# STEP 5: Run your tool (customize for your workflow)
# =========================================================================

log("")
log("=" * 60)
log("STEP 5: Run tool")
log("=" * 60)

# EXAMPLE: Run a simple samtools command
# For xTea or any other tool, replace with actual command
tool_cmd = f"""
samtools view {local_cram} chr1:1-1000000 | head -20
"""

log(f"  sample_id: {SAMPLE_ID}")
log(f"  working directory: {WORK_DIR}")

# Start tool in background so we can checkpoint
tool_log = WORK_DIR / "tool_run.log"
tool_proc = subprocess.Popen(
    tool_cmd,
    shell=True,
    stdout=open(tool_log, "w"),
    stderr=subprocess.STDOUT,
)

# =========================================================================
# STEP 6: Checkpoint loop (monitor while tool runs)
# =========================================================================

log("")
log("=" * 60)
log("STEP 6: Run with periodic checkpointing")
log("=" * 60)

def checkpoint_loop():
    """Periodically save logs and disk status."""
    iteration = 0
    while tool_proc.poll() is None:
        time.sleep(CHECKPOINT_INTERVAL)
        iteration += 1
        
        try:
            # Disk usage
            disk_out = subprocess.run("df -h /", shell=True, capture_output=True, text=True).stdout
            log(f"[CKPT {iteration}] Disk usage:")
            for line in disk_out.strip().split("\n"):
                log(f"  {line}")
            
            # Tool log tail
            if tool_log.exists():
                with open(tool_log) as f:
                    lines = f.readlines()
                log(f"[CKPT {iteration}] Tool output tail ({len(lines)} lines):")
                for line in lines[-20:]:
                    log(f"  {line.rstrip()}")
            
            # Upload checkpoint
            upload_logs(TOKEN)
        except Exception as e:
            log(f"[CKPT {iteration}] Error: {e}")

# Start checkpoint thread
checkpoint_thread = threading.Thread(target=checkpoint_loop, daemon=True)
checkpoint_thread.start()

# Wait for tool to finish
tool_proc.wait()
rc = tool_proc.returncode
log(f"  tool exit code: {rc}")

# Show final log
if tool_log.exists():
    log("")
    log("=" * 60)
    log("Tool output (final)")
    log("=" * 60)
    with open(tool_log) as f:
        for line in f.readlines()[-50:]:
            log(f"  {line.rstrip()}")

upload_logs(TOKEN)

# =========================================================================
# STEP 7: Validate output and upload
# =========================================================================

log("")
log("=" * 60)
log("STEP 7: Upload outputs")
log("=" * 60)

# Check for expected output file (customize)
expected_output = WORK_DIR / "output.txt"  # Or .vcf, .bam, etc.

if expected_output.exists():
    log(f"  ✓ Output file found: {expected_output.name}")
    
    # Upload outputs
    for fpath in WORK_DIR.rglob("*"):
        if not fpath.is_file():
            continue
        if fpath.name.startswith("."):
            continue
        
        rel = fpath.relative_to(WORK_DIR)
        gcs_key = f"output/{SAMPLE_ID}/{rel}"
        
        try:
            gcs_upload(str(fpath), gcs_key, TOKEN)
        except Exception as e:
            log(f"  WARN: Failed to upload {rel}: {e}")
    
    log(f"  ✓ All outputs uploaded to gs://{BUCKET}/output/{SAMPLE_ID}/")
    upload_logs(TOKEN)
    
    log("")
    log("=" * 60)
    log("SUCCESS")
    log("=" * 60)
    sys.exit(0)

else:
    log(f"  ERROR: Expected output not found: {expected_output}")
    log(f"  Contents of {WORK_DIR}:")
    for fpath in sorted(WORK_DIR.rglob("*"))[:50]:
        log(f"    {fpath.relative_to(WORK_DIR)}")
    
    upload_logs(TOKEN)
    log("")
    log("=" * 60)
    log("FAILURE: No output produced")
    log("=" * 60)
    sys.exit(1)
