# Google Batch on Verily Workbench

A reference implementation for submitting containerized workflows to Google Batch via Verily Workbench. 

---

## What's in here

- **GUIDE_GoogleBatch_Workbench.md** — Step-by-step walkthrough: authentication, job submission, monitoring, cost management
- **examples/worker_template.py** — Reusable worker pattern for any containerized job (downloads inputs from GCS, runs your code, uploads outputs). Ships with a simple, runnable samtools example so you can test the whole pipeline end to end before plugging in a heavier tool.
- **examples/job_config_template.json** — Google Batch configuration template with all required fields
- **TROUBLESHOOTING.md** — Common problems and solutions, so you don't need to spend that much time troubleshooting things as I did :) 
- **scripts/submit_batch_job.py** — Helper script to submit jobs without hand-crafting JSON

---

## Quick start (30 seconds)

```bash
# 1. Authenticate
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>

# 2. Submit a test job (runs the built-in samtools example)
python scripts/submit_batch_job.py \
  --project <YOUR_PROJECT_ID> \
  --region <YOUR_REGION> \
  --sample-id TEST_001 \
  --image quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0 \
  --worker-script gs://<YOUR_BUCKET>/scripts/worker.py \
  --env CRAM_PATH=gs://<YOUR_BUCKET>/input/TEST_001/TEST_001.cram

# 3. Monitor
gcloud batch jobs list --project=<YOUR_PROJECT_ID> --location=<YOUR_REGION>
gcloud batch jobs describe JOB_NAME --location=<YOUR_REGION>

# 4. Get logs
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/* | grep JOB_NAME | tail -1)
```

---

## Next steps

1. Read **GUIDE_GoogleBatch_Workbench.md** for a detailed walkthrough
2. Copy **examples/worker_template.py** and adapt it to your tool
3. Test on a single sample with **examples/job_config_template.json**
4. Scale up using **scripts/submit_batch_job.py**
5. Refer to **TROUBLESHOOTING.md** if things break
