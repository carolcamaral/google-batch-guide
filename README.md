# Google Batch on Verily Workbench — A practical guide

A reference implementation for submitting containerized scientific workflows to Google Batch via Verily Workbench. Based on production xTea (transposable element detection) jobs running on GP2 WGS data.

**Status:** Validated on 27 May 2026. xTea pipeline: 50/71 TONIC-PERRON samples complete (Alu, L1, SVA).

---

## What's in here

- **GUIDE_GoogleBatch_Workbench.md** — Step-by-step walkthrough: authentication, job submission, monitoring, cost management
- **examples/worker_template.py** — Reusable worker pattern for any containerized job (downloads inputs from GCS, runs your code, uploads outputs)
- **examples/job_config_template.json** — Google Batch configuration template with all required fields
- **examples/xtea_case_study/** — Complete working example: how we set up and scale xTea
- **TROUBLESHOOTING.md** — Common problems and solutions
- **scripts/submit_batch_job.py** — Helper script to submit jobs without hand-crafting JSON

---

## Quick start (30 seconds)

```bash
# 1. Authenticate
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>

# 2. Submit a test job
python scripts/submit_batch_job.py \
  --sample-id TEST_001 \
  --repeat-type Alu \
  --cram-path gs://your-bucket/WGS/TEST_001/TEST_001.cram

# 3. Monitor
gcloud batch jobs list --project=<YOUR_PROJECT_ID> --location=<YOUR_REGION>
gcloud batch jobs describe JOB_NAME --location=<YOUR_REGION>

# 4. Get logs
gsutil cat gs://<YOUR_BUCKET>/logs/xtea_TEST_001_Alu_*.txt | tail -50
```

---

## What works

✅ **Container runtime:** quay.io/biocontainers/xtea:0.1.9 (any container from quay.io; docker.io is VPC SC blocked)

✅ **VM size:** e2-highmem-16 (16 vCPU, 128 GB RAM) — the consensus step in xTea requires ~100 GB

✅ **Auth:** Metadata server token + urllib (no credential files needed in container)

✅ **Output:** upload to GCS via urllib, checkpointing every 10 min

✅ **Parallelism:** submit 100+ jobs at once, each with independent VM

---

## What doesn't work (and why)

❌ **docker.io** — VPC Service Controls blocks Docker Hub; use quay.io instead

❌ **External IPs** — Workbench VPC isolates compute; must use VPC-internal DNS and service accounts

❌ **Local file references in container** — container can't see the host; use `--bind` mounts or download to `/tmp`

❌ **Job names with underscore** — Batch only accepts `[a-z0-9-]`; use hyphens

---

## Cost

- **Compute:** e2-highmem-16 @ ~$0.54/h (europe-west4) = ~$1.44 per sample for 4 TE types (L1, Alu, SVA, HERV) running in parallel
- **Storage (GCS at rest):** ~$0.02/GiB/month (negligible; our ref/ is ~13 GiB = $0.26/month)
- **Egress:** free within Google Cloud, charged only outside (request-pays GCS bucket costs money)

---

## Architecture

```
Workbench Jupyter
  └─ Create sample setup via xTea CLI (local, once per cohort)
  └─ Sync to GCS bucket
  └─ Submit Google Batch job (gcloud CLI)
     └─ Google Batch VM (e2-highmem-16)
        └─ Worker script (Python)
           ├─ Auth via metadata server
           ├─ Download reference files
           ├─ Download input data (CRAM)
           ├─ Run containerized tool (xTea, GATK, etc)
           ├─ Checkpoint periodically (upload logs)
           └─ Upload outputs to GCS
```

---

## Key learnings

1. **Worker scripts should fail honestly:** if your tool doesn't produce output, `sys.exit(1)`. We found SUCCEEDED can mean "produced nothing" — masking bugs.

2. **Checkpointing matters:** save logs every 10 min to GCS during execution. If the job gets evicted at hour 5, you'll know where it stuck.

3. **Patches are inevitable:** the BioContainers xTea image has `BusyBox sort` (no `sort -k1,1V` support), missing Java (bamsnap), numpy aliasing issues. Our worker handles this; you'll need equivalent patches for your tool.

4. **Token expiry is not a blocker for <6h jobs:** metadata server tokens refresh automatically on first use; we've successfully uploaded terabytes in parallel xTea jobs without hitting token expiry.

5. **RAM is the constraint, not CPU:** xTea consensus step needs ~100 GB RAM regardless of thread count. L1 genotyping needs ~90 GB. Design your VM around RAM, not threads.

---

## Next steps

1. Read **GUIDE_GoogleBatch_Workbench.md** for a detailed walkthrough
2. Copy **examples/worker_template.py** and adapt it to your tool
3. Test on a single sample with **examples/job_config_template.json**
4. Scale up using **scripts/submit_batch_job.py**
5. Refer to **TROUBLESHOOTING.md** if things break

---

## Contact / Contributing

Found a bug or have a suggestion? Open an issue or contact the GP2 bioinformatics team.

**Repository:** [https://github.com/<YOUR_USERNAME>/google-batch-guide](https://github.com/<YOUR_USERNAME>/google-batch-guide)

**Reference:** xTea paper — Chu C. et al., *Comprehensive identification of transposable element insertions using multiple sequencing technologies*, Nat Commun (2021)

---

**Last updated:** 27 May 2026 | **Status:** Production validated
