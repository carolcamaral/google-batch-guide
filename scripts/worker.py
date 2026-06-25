#!/usr/bin/env python3
"""
worker.py  —  OPTION B: run the SAME analysis with a Batch worker.

Compare this with OPTION A (run_dsub.sh). The analysis is identical (it ends
up calling the same inspect() logic). The difference is everything *around*
it: here YOU write the code that

  1. gets an auth token from the VM metadata server,
  2. downloads the input from GCS (retrying with a billing project so that
     requester-pays buckets like GP2's gp2_crams work),
  3. runs the analysis,
  4. uploads the report back to GCS.

dsub does steps 1, 2 and 4 for you. The worker is more code, but you control
every step (e.g. you could download only part of the file, add checkpoints,
or branch on the input). That control is the whole reason to choose a worker
over dsub.

Environment variables (set by the Batch job config):
  - SAMPLE_ID
  - CRAM_PATH             : gs:// path to the input CRAM
  - OUTPUT_BUCKET         : bucket (no gs://) to write the report to
  - GOOGLE_CLOUD_PROJECT  : billing project for requester-pays downloads
"""

import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SAMPLE_ID = os.environ.get("SAMPLE_ID", "SAMPLE")
CRAM_PATH = os.environ["CRAM_PATH"]
OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

LOCAL_CRAM = "/tmp/input.cram"
LOCAL_REPORT = "/tmp/report.txt"


# ---- the GCS plumbing that dsub would otherwise do for you -------------------

def get_token():
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/"
        "instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=10).read())["access_token"]


def gcs_download(gs_path, local_path, token, user_project=None):
    bucket, obj = gs_path[len("gs://"):].split("/", 1)
    url = (
        "https://storage.googleapis.com/download/storage/v1/b/"
        f"{bucket}/o/{urllib.parse.quote(obj, safe='')}?alt=media"
    )
    if user_project:
        url += f"&userProject={user_project}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=600) as resp, open(local_path, "wb") as f:
        f.write(resp.read())


def gcs_upload(local_path, bucket, key, token):
    with open(local_path, "rb") as f:
        data = f.read()
    url = (
        "https://storage.googleapis.com/upload/storage/v1/b/"
        f"{bucket}/o?uploadType=media&name={urllib.parse.quote(key, safe='')}"
    )
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/plain"},
    )
    urllib.request.urlopen(req, timeout=300)


# ---- the SAME analysis as inspect_cram.py -----------------------------------

def inspect(input_path):
    size = os.path.getsize(input_path)
    with open(input_path, "rb") as f:
        magic = f.read(4)
    return {"size_bytes": size, "first_4_bytes": repr(magic),
            "looks_like_cram": magic == b"CRAM"}


def main():
    print(f"[worker] sample={SAMPLE_ID} cram={CRAM_PATH}", flush=True)
    token = get_token()

    # 1+2. Download the input. Try direct, then retry with a billing project
    #      (this is what makes the requester-pays GP2 bucket work).
    print("[worker] downloading CRAM...", flush=True)
    try:
        gcs_download(CRAM_PATH, LOCAL_CRAM, token)
    except Exception as e:
        print(f"[worker] direct download failed ({e}); retry with userProject", flush=True)
        gcs_download(CRAM_PATH, LOCAL_CRAM, token, user_project=PROJECT_ID)

    # 3. Run the analysis (identical to inspect_cram.py).
    facts = inspect(LOCAL_CRAM)
    report = (
        f"inspect_cram report\n"
        f"generated:  {datetime.now(timezone.utc).isoformat()}\n"
        f"sample_id:  {SAMPLE_ID}\n"
        f"input:      {CRAM_PATH}\n"
        f"size_bytes: {facts['size_bytes']}\n"
        f"first_bytes:{facts['first_4_bytes']}\n"
        f"is_cram:    {facts['looks_like_cram']}\n"
    )
    with open(LOCAL_REPORT, "w") as f:
        f.write(report)
    print(report, flush=True)

    # 4. Upload the report.
    key = f"output/{SAMPLE_ID}/report.txt"
    gcs_upload(LOCAL_REPORT, OUTPUT_BUCKET, key, token)
    print(f"[worker] wrote gs://{OUTPUT_BUCKET}/{key}", flush=True)


if __name__ == "__main__":
    main()
