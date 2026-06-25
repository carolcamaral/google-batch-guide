# Troubleshooting — Google Batch on Verily Workbench

Common problems and solutions.

---

## Job Submission

### "Authentication failed" / "permission denied"

**Check:**
```bash
gcloud auth list
gcloud config get-value project
```

**Solutions:**
- Run `gcloud auth login` and follow the browser prompt
- Verify service account has required roles: `roles/batch.admin`, `roles/compute.admin`, `roles/storage.objectAdmin`
- Check that you're in the right project: `gcloud config set project <YOUR_PROJECT_ID>`

---

### "Invalid job name" / job name has underscores

**Problem:** Google Batch only accepts names matching `[a-z0-9-]+`.

**Solution:** Use `submit_batch_job.py` (handles this automatically), or sanitize manually:
```bash
# Good
gcloud batch jobs submit my-job-001 ...

# Bad (will fail)
gcloud batch jobs submit my_job_001 ...
```

---

### "Network not found" / "subnetwork not found"

**Problem:** The network/subnetwork doesn't exist or your service account doesn't have access.

**Check:**
```bash
gcloud compute networks list --project=<YOUR_PROJECT_ID>
gcloud compute networks subnets list --project=<YOUR_PROJECT_ID>
```

**Solution:**
- Update `job_config.json` with the actual network/subnetwork names
- Or create a new network:
  ```bash
  gcloud compute networks create my-vpc --project=<YOUR_PROJECT_ID>
  gcloud compute networks subnets create my-subnet --network=my-vpc --region=<YOUR_REGION>
  ```

---

### "Quota exceeded" / can't allocate VM

**Problem:** Project hit quota limits (e.g., can't allocate 10 e2-highmem-16 VMs at once).

**Check:**
```bash
gcloud compute project-info describe --project=<YOUR_PROJECT_ID> --format="value(quotas[].usage)"
```

**Solutions:**
- Submit jobs one at a time instead of 100 in parallel
- Use `--async` and stagger submissions:
  ```bash
  for i in {1..50}; do
    gcloud batch jobs submit job-$i ... --async
    sleep 2
  done
  ```
- Request quota increase from Google Cloud Console

---

## Job Execution

### Job shows "RUNNING" or "QUEUED" for hours

**Problem:** VM allocation or container pull is stuck.

**Check:**
```bash
gcloud batch tasks describe 0 \
  --job=<JOB_NAME> \
  --task_group=group0 \
  --format="value(status.state)" \
  --project=<YOUR_PROJECT_ID>
```

**Solutions:**
- Check if the container image exists:
  ```bash
  gcloud container images list --repository=quay.io/biocontainers 2>&1 | grep samtools
  ```
- If pulling from quay.io, it should succeed (quay.io is VPC SC allowed)
- If using docker.io, the pull will hang because it's VPC SC blocked
- Wait a bit longer (VM allocation can take 2-5 min)
- Cancel and resubmit if it exceeds your `maxRunDuration`

---

### Job shows "FAILED" with exit code 1

**Problem:** Tool exited with error.

**Check logs:**
```bash
# Find the latest checkpoint log
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/worker_SAMPLE_*.txt | sort | tail -1)

# Or view all Cloud Logging logs
gcloud logging read "resource.type=batch.googleapis.com AND labels.job_id=<JOB_ID>" --limit=100
```

**Common causes:**
- Reference files not downloaded (permission denied, path wrong)
- Input CRAM not found or corrupted
- Tool crashed (check stderr in logs)
- Disk full (check df output in checkpoints)

**Fix:**
1. Read the log to identify the exact error
2. Fix the issue (e.g., correct GCS path, upload missing ref file)
3. Resubmit the job

---

### Job shows "SUCCEEDED" but no output in GCS

**Problem:** Worker exited with code 0 even though the tool failed.

**Root cause:** The worker script doesn't validate that output was produced.

**Solution:** Edit worker script to check for expected output before exiting 0:
```python
# At the end of worker.py, before sys.exit(0):
expected_output = WORK_DIR / f"{SAMPLE_ID}.stats.txt"
if not expected_output.exists() or expected_output.stat().st_size == 0:
    log("ERROR: No output produced")
    sys.exit(1)
```

This is already in `worker_template.py` — ensure your adaptation includes this check.

---

### "Disk quota exceeded" or "No space left on device"

**Problem:** 200 GB boot disk filled up during job.

**Check:**
```bash
# From checkpoint logs, look for df output
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/worker_*.txt | tail -1) | grep "Disk usage" -A5
```

**Solutions:**
- Increase boot disk: change `bootDisk.sizeGb` to 500 GB in job config
- Delete intermediate files during tool execution (e.g., `rm -rf /tmp/work/tmp/*`)
- Use smaller batch size (process fewer samples in parallel)
- Consolidate 4 TE types into 1 job (fewer repeated downloads)

---

### "No such file or directory" when uploading outputs

**Problem:** Worker tried to upload a file that doesn't exist.

**Cause:** Tool didn't produce expected output.

**Check:** Review tool log tail in the latest checkpoint log. Look for error messages.

**Fix:**
1. Verify tool ran correctly (check exit code)
2. Verify tool used correct input paths
3. Verify reference files were downloaded
4. Re-run locally in Workbench for comparison

---

## GCS Access

### "Access Denied" when downloading CRAM

**Problem:** CRAM is in a requester-pays bucket but the job wasn't configured for it.

**Check:**
```bash
gsutil -u <YOUR_PROJECT_ID> ls gs://gp2_crams/WGS/SAMPLE_001/
```

**Solution:** Add `userProject` to the download command in worker:
```python
gcs_download(
    cram_path,
    local_path,
    token,
    user_project="<YOUR_PROJECT_ID>"
)
```

This is already in `worker_template.py` — ensure your adaptation includes it.

---

### "Google Cloud Storage bucket not found"

**Problem:** Typo in bucket name.

**Check:**
```bash
gsutil ls gs://<YOUR_BUCKET>/
```

**Solution:** Correct the bucket name in environment variables and job config.

---

### Logs not uploading (checkpoints missing)

**Problem:** Worker script crashes before uploading logs, or GCS write fails.

**Workaround:** Logs are still available via Cloud Logging:
```bash
gcloud logging read "resource.labels.job_id=<JOB_ID>" --limit=100
```

**Fix:** Ensure `upload_logs()` is called in worker script even on error (wrap in try/except).

---

## Container Issues

### "ImageUri not found"

**Problem:** Container image doesn't exist at that registry.

**Check:**
```bash
gcloud container images list-tags quay.io/biocontainers/samtools
```

**Solutions:**
- Use a different image tag (e.g., `:latest`)
- Pull from a public registry (quay.io, gcr.io, ghcr.io)
- If using a private registry, ensure service account has `roles/artifactregistry.reader`

---

### "docker.io not accessible" / "pull timed out"

**Problem:** VPC Service Controls blocks docker.io.

**Solution:** Use an allowed registry:
- ✅ `quay.io/biocontainers/...`
- ✅ `gcr.io/...` (Google Container Registry)
- ✅ `ghcr.io/...` (GitHub Container Registry)
- ❌ `docker.io/...` (Docker Hub — blocked)

---

### "BusyBox: command not found"

**Problem:** Container has a minimal base image (busybox) without common tools.

**Examples:**
- `sort: unknown sort type -V` → container sort doesn't support version sort
- `curl: command not found` → use Python urllib instead

**Solution:** Use `quay.io/biocontainers/*` images (they're larger but have standard tools)

---

## Still stuck?

1. **Check Cloud Logging:**
   ```bash
   gcloud logging read "resource.type=batch.googleapis.com" \
     --limit=50 \
     --format=json
   ```

2. **Check Cloud Monitoring:**
   Cloud Console → Monitoring → Metrics Explorer → filter by job ID

3. **SSH into the VM while it's running** (advanced):
   ```bash
   gcloud compute instances list \
     --filter="labels.batch-job-id=<JOB_ID>"
   gcloud compute ssh INSTANCE_NAME --zone=<ZONE>
   # Poke around in /tmp/work/, check processes, etc.
   ```

---

## Summary table

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Job won't submit | Invalid config, missing quotas | Check gcloud auth, quotas, job name format |
| Job stuck in QUEUED | VM allocation slow, container pull slow | Wait, or check if image exists |
| Job FAILED with exit 1 | Tool crashed or reference files missing | Check logs for actual error |
| Job SUCCEEDED but no output | Worker didn't validate output | Add output check before exit 0 |
| "Access Denied" to CRAM | Requester-pays bucket | Use `userProject` in download |
| Memory exceeded | Tool too large for VM | Increase VM size or reduce parallelism |
| Disk full | Output files large | Increase bootDisk.sizeGb |
| Logs missing | Worker crashed before upload | Wrap upload in try/except |
