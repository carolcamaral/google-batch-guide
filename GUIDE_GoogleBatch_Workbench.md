# Google Batch on Verily Workbench — Complete Guide

A step-by-step walkthrough of submitting containerized jobs to Google Batch from Verily Workbench.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Understanding the Architecture](#understanding-the-architecture)
3. [Setting Up Your First Job](#setting-up-your-first-job)
4. [Submitting a Job](#submitting-a-job)
5. [Monitoring and Debugging](#monitoring-and-debugging)
6. [Managing Costs](#managing-costs)
7. [Common Patterns](#common-patterns)

---

## Prerequisites

### Access
- Verily Workbench project with Google Cloud integration enabled
- Service account with roles:
  - `roles/batch.admin` (submit and manage jobs)
  - `roles/compute.admin` (allocate VMs)
  - `roles/storage.objectAdmin` (read/write GCS buckets)
  - `roles/logging.logWriter` (send logs to Cloud Logging)
- GCS bucket for input data, reference files, and outputs (recommend `<YOUR_BUCKET>`)

### Tools
```bash
# Install gcloud SDK if not already present
curl https://sdk.cloud.google.com | bash
exec -l $SHELL
gcloud init

# Verify
gcloud --version
gcloud auth list
```

### Network
- Understand that Workbench uses a **private VPC** — compute VMs cannot reach external IPs
- All data access must be via **internal GCS** or **requester-pays buckets** with proper authentication
- DNS names resolve via internal Google DNS

---

## Understanding the Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ Verily Workbench (Jupyter VM on private VPC)                    │
│                                                                 │
│  1. Prepare input data, ref files                               │
│  2. Sync to GCS bucket                                          │
│  3. Write job config (JSON)                                     │
│  4. Submit via gcloud API                                       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ gcloud batch jobs submit
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ Google Batch (manages job scheduling, VM allocation)            │
│                                                                 │
│  ├─ Allocate e2-highmem-16 VM                                   │
│  ├─ Pull container from quay.io                                 │
│  ├─ Run worker script inside container                          │
│  └─ Clean up VM when done                                       │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ worker script
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ Container (e.g., quay.io/biocontainers/xtea:0.1.9)              │
│                                                                 │
│  1. Authenticate via metadata server                            │
│  2. Download reference files from GCS                           │
│  3. Download input data (CRAM, etc)                             │
│  4. Run your tool (xTea, GATK, bcftools, etc)                   │
│  5. Upload outputs back to GCS                                  │
│  6. Upload logs (checkpointed every 10 min)                     │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       │ gsutil / urllib
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ GCS Bucket (<YOUR_BUCKET>)                                      │
│                                                                 │
│  ├─ ref/                   (input: reference files)             │
│  ├─ input/                 (input: CRAMs, BAMs)                 │
│  ├─ xtea_output/           (output: VCFs, intermediates)        │
│  └─ logs/                  (checkpoints during execution)       │
└─────────────────────────────────────────────────────────────────┘
```

### Worker Script Pattern

Every job uses a **worker script** that:
1. Gets an auth token from the metadata server (no credentials file needed)
2. Downloads input data from GCS
3. Runs your containerized tool
4. Uploads results back to GCS
5. Periodically checkpoints (saves logs to GCS every 10 min)

This pattern is **independent of your tool** — adapt `examples/worker_template.py` for any job.

---

## Setting Up Your First Job

### Step 1: Prepare reference files and input data

In Workbench Jupyter, prepare your inputs:

```python
# Example: check what files you need
import subprocess
import os

bucket = "gs://<YOUR_BUCKET>"
sample_id = "SAMPLE_001"

# Ensure reference files are in GCS
subprocess.run(f"gsutil ls {bucket}/ref/", shell=True)

# Ensure input CRAM is in GCS
cram_path = f"{bucket}/input/{sample_id}/{sample_id}.cram"
crai_path = f"{cram_path}.crai"
subprocess.run(f"gsutil ls {cram_path} {crai_path}", shell=True)
```

### Step 2: Prepare tool setup (one-time)

If your tool requires a setup phase (like xTea does), do it once in Workbench and sync to GCS:

```bash
# Example for xTea (pseudo-code)
cd /home/jupyter/workspace/ws_files

# Run setup locally
python xTea/bin/xtea \
  -i sample_list.txt \
  -b bam_list.txt \
  ... (other flags) \
  -p path_work_folder/

# Sync to bucket
gsutil -m cp -r path_work_folder/ gs://<YOUR_BUCKET>/path_work_folder/
gsutil -m cp -r xTea/ gs://<YOUR_BUCKET>/xTea/
gsutil -m cp -r ref/ gs://<YOUR_BUCKET>/ref/
```

### Step 3: Write job configuration

Copy `examples/job_config_template.json` and customize:

```json
{
  "taskGroups": [{
    "taskSpec": {
      "runnables": [{
        "container": {
          "imageUri": "quay.io/biocontainers/xtea:0.1.9--hdfd78af_0",
          "entrypoint": "/bin/bash",
          "commands": [
            "-c",
            "python -c \"import urllib.request; urllib.request.urlopen(urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',headers={'Metadata-Flavor':'Google'})).read()\" && python /tmp/worker.py"
          ]
        }
      }],
      "environment": {
        "variables": {
          "SAMPLE_ID": "SAMPLE_001",
          "REPEAT_TYPE": "Alu",
          "CRAM_PATH": "gs://<YOUR_BUCKET>/input/SAMPLE_001/SAMPLE_001.cram",
          "GOOGLE_CLOUD_PROJECT": "<YOUR_PROJECT_ID>"
        }
      },
      "computeResource": {
        "cpuMilli": "16000",
        "memoryMib": "122880"
      },
      "maxRetryCount": 0,
      "maxRunDuration": "36000s"
    },
    "taskCount": 1
  }],
  "allocationPolicy": {
    "instances": [{
      "policy": {
        "machineType": "e2-highmem-16",
        "bootDisk": {"sizeGb": 200}
      }
    }],
    "network": {
      "networkInterfaces": [{
        "network": "projects/<YOUR_PROJECT_ID>/global/networks/<YOUR_NETWORK_NAME>",
        "subnetwork": "projects/<YOUR_PROJECT_ID>/regions/<YOUR_REGION>/subnetworks/<YOUR_SUBNETWORK_NAME>",
        "noExternalIpAddress": true
      }]
    },
    "location": {"allowedLocations": ["regions/<YOUR_REGION>"]},
    "serviceAccount": {"email": "<YOUR_SERVICE_ACCOUNT_EMAIL>"}
  },
  "logsPolicy": {"destination": "CLOUD_LOGGING"}
}
```

Key fields:
- `cpuMilli`: 16000 = 16 vCPUs (for xTea with `-n 8`, keep this)
- `memoryMib`: 122880 = 120 GB RAM (xTea consensus step needs ~100 GB minimum)
- `bootDisk`: 200 GB (CRAM ~12 GB + ref ~15 GB + tmp ~30 GB + buffer)
- `maxRunDuration`: 36000s = 10 hours (adjust based on your tool; xTea = 30 min to 5 h depending on sample)
- `noExternalIpAddress`: true (required for VPC SC)

---

## Submitting a Job

### Option A: Via gcloud CLI (manual)

```bash
JOB_NAME="xtea-sample-001-alu-$(date +%s)"

gcloud batch jobs submit "${JOB_NAME}" \
  --project=<YOUR_PROJECT_ID> \
  --location=<YOUR_REGION> \
  --config=job_config.json
```

### Option B: Via Python helper script (recommended)

```bash
python scripts/submit_batch_job.py \
  --project <YOUR_PROJECT_ID> \
  --region <YOUR_REGION> \
  --sample-id SAMPLE_001 \
  --repeat-type Alu \
  --cram-path gs://<YOUR_BUCKET>/input/SAMPLE_001/SAMPLE_001.cram
```

This script:
- Generates a unique job name (no underscore issues)
- Fills in placeholders from your config
- Submits automatically
- Returns job name and monitoring command

### Option C: Batch submission (many samples)

```bash
# For each sample in a list
for sample in $(cat sample_list.txt); do
  python scripts/submit_batch_job.py \
    --sample-id "$sample" \
    --repeat-type Alu &
  sleep 2  # stagger submissions
done
wait
```

This submits 50+ jobs in parallel. Batch will schedule them as resources become available.

---

## Monitoring and Debugging

### Check job status

```bash
# List all jobs
gcloud batch jobs list \
  --project=<YOUR_PROJECT_ID> \
  --location=<YOUR_REGION>

# Describe a specific job
gcloud batch jobs describe JOB_NAME \
  --project=<YOUR_PROJECT_ID> \
  --location=<YOUR_REGION> \
  --format=yaml

# Check task status
gcloud batch tasks describe 0 \
  --project=<YOUR_PROJECT_ID> \
  --location=<YOUR_REGION> \
  --job=JOB_NAME \
  --task_group=group0
```

### Get logs (checkpoints)

Worker script uploads logs every 10 min. Find them:

```bash
# List checkpoint logs
gsutil ls gs://<YOUR_BUCKET>/logs/xtea_SAMPLE_001_Alu_*.txt | sort

# View latest
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/xtea_SAMPLE_001_Alu_*.txt | tail -1)

# Tail last 50 lines
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/xtea_SAMPLE_001_Alu_*.txt | tail -1) | tail -50
```

### Debug a stuck job

1. Get the latest log checkpoint:
   ```bash
   gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/xtea_SAMPLE_001_Alu_*.txt | tail -1)
   ```

2. Look for the last completed step (should show time/progress)

3. If task is still running:
   ```bash
   gcloud batch tasks describe 0 \
     --job=JOB_NAME \
     --format="value(status.state)"
   ```

4. If FAILED and exit code shows non-zero:
   ```bash
   gcloud batch tasks describe 0 \
     --job=JOB_NAME \
     --format="value(status.runnable.exitCode)"
   ```

5. SSH into the VM while it's running (advanced):
   ```bash
   gcloud compute instances list --filter="labels.batch-job-id=JOB_ID"
   gcloud compute ssh INSTANCE_NAME --zone=<ZONE>
   # Poke around in /tmp/work/
   ```

### Check outputs

```bash
# List outputs for a sample
gsutil ls gs://<YOUR_BUCKET>/xtea_output/SAMPLE_001/Alu/

# Download a specific VCF
gsutil cp gs://<YOUR_BUCKET>/xtea_output/SAMPLE_001/Alu/*.vcf local_file.vcf
```

---

## Managing Costs

### Cost drivers

| Item | Cost | Mitigation |
|------|------|-----------|
| **VM compute (e2-highmem-16)** | ~$0.54/h | Use only when needed; batch jobs, not interactive |
| **Storage (at rest)** | $0.02/GiB/month | Delete old outputs; keep only VCF + metadata |
| **Egress (out of GCP)** | $0.12/GiB | Use requester-pays buckets; stay inside GCP for transfers |
| **Requester-pays buckets** | ~$0.004/GiB read | Negotiate at the source (e.g., GP2 may absorb cost) |

### Strategies

1. **Consolidate TE types:** instead of 4 jobs per sample (Alu, L1, SVA, HERV separately), run them together with `-y 15` (if RAM allows). 70 samples × 4 types = 280 jobs → 70 jobs.

2. **Clean up outputs aggressively:** keep `.vcf` and metadata, delete intermediate `.bam`, `.sam`, raw alignments. Saves ~80% of storage.

3. **Use spot VMs (experimental):** `e2-spot` saves ~70% on compute but can be preempted. Not recommended for long-running jobs, but OK if your tool checkpoints.

4. **Monitor per-project usage:**
   ```bash
   gcloud billing accounts list
   gcloud billing budgets list --billing-account=<BILLING_ACCOUNT>
   ```

5. **Set up alerts:** Cloud Monitoring can email you if hourly compute cost exceeds a threshold.

---

## Common Patterns

### Pattern 1: Parallel submission with different inputs

```bash
# Submit 100 jobs, each with a different CRAM
for sample in $(cat samples.txt | head -100); do
  python scripts/submit_batch_job.py \
    --sample-id "$sample" \
    --cram-path "gs://<YOUR_BUCKET>/input/${sample}/${sample}.cram" \
    --repeat-type Alu &
  
  # Stagger to avoid quota exhaustion
  sleep 1
done
wait

echo "Submitted 100 jobs. Monitor with: gcloud batch jobs list --project=<YOUR_PROJECT_ID>"
```

### Pattern 2: Retry failed jobs

```bash
# List samples with no output VCF
gsutil ls gs://<YOUR_BUCKET>/xtea_output/*/Alu/*.vcf | \
  cut -d/ -f6 | sort -u > done_samples.txt

# Compare with all samples
comm -23 <(sort all_samples.txt) done_samples.txt > failed_samples.txt

# Resubmit
for sample in $(cat failed_samples.txt); do
  python scripts/submit_batch_job.py \
    --sample-id "$sample" \
    --repeat-type Alu &
done
wait
```

### Pattern 3: Chain jobs (output of one is input to next)

```bash
# Example: align → call → filter

# 1. Submit alignment jobs, capture job IDs
for sample in $(cat samples.txt); do
  gcloud batch jobs submit "align-$sample" \
    --config=align_config.json \
    --project=<YOUR_PROJECT_ID> \
    --async
done

# 2. Wait for all align jobs to finish
# (polling every 5 min)
while true; do
  pending=$(gcloud batch jobs list --filter="name:align-* AND status.state:RUNNING OR status.state:QUEUED" --format="value(name)" | wc -l)
  if [ "$pending" -eq 0 ]; then break; fi
  echo "$(date): $pending jobs still running"
  sleep 300
done

# 3. Submit calling jobs
for sample in $(cat samples.txt); do
  python scripts/submit_batch_job.py --sample-id "$sample" --repeat-type Alu &
done
```

### Pattern 4: Dynamic config based on sample metadata

```python
# In Python (Workbench)
import json
import pandas as pd

# Load sample metadata
metadata = pd.read_csv("sample_metadata.csv")  # cols: sample_id, cram_path, organism, depth

for _, row in metadata.iterrows():
    config = {
        "taskGroups": [{
            "taskSpec": {
                "environment": {
                    "variables": {
                        "SAMPLE_ID": row["sample_id"],
                        "CRAM_PATH": row["cram_path"],
                        "ORGANISM": row["organism"],
                        # Adjust memory based on sequencing depth
                        "MEMORY_MB": 122880 if row["depth"] > 50 else 65536
                    }
                },
                # ... rest of config
            }
        }]
    }
    
    # Save and submit
    with open(f"config_{row['sample_id']}.json", "w") as f:
        json.dump(config, f)
```

---

## Troubleshooting

**Q: "docker.io" rejected. Where do I find containers?**
A: Use `quay.io` (BioContainers), `gcr.io`, or other registries not blocked by VPC Service Controls. `docker.io` (Docker Hub) is not accessible from inside the VPC.

**Q: "no external IP" or "network timeout"**
A: You're inside a private VPC. Remove `noExternalIpAddress: false` and ensure your network/subnet allow internal GCS access. Check that service account has `roles/storage.objectViewer`.

**Q: Job shows SUCCEEDED but no output**
A: Your worker script exited with code 0 even when the tool failed. Fix: add a final check like `if [ ! -f output.vcf ]; then exit 1; fi` before exiting successfully.

**Q: "Disk quota exceeded"**
A: 200 GB boot disk is too small for your inputs. Try 500 GB or consolidate jobs so you don't run in parallel. Or delete intermediates during job (clean up `/tmp` in worker).

**Q: "max retries exceeded" / job keeps failing**
A: Set `maxRetryCount: 0` and debug first (check logs). Fix the actual issue, then resubmit. Retries without fixing root cause waste money.

**Q: Logs not uploading (checkpoints missing)**
A: Worker script has a bug in the checkpoint loop. Check that `upload_log()` is being called. If network issue, worker can still complete and upload final logs on exit.

For more, see **TROUBLESHOOTING.md**.

---

## Next Steps

1. Adapt `examples/worker_template.py` for your tool
2. Test with a single sample first
3. Expand to batch submission once confident
4. Monitor costs and adjust VM size / time limits as needed
5. Share back improvements to the team!

