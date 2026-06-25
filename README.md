# Running a containerized job on Google Batch from Verily Workbench

Two ways to run the **same** analysis on a CRAM stored in Google Cloud Storage,
from inside a Verily Workbench compute environment:

- **Option A — dsub.** One command. dsub downloads the input, runs your code,
  and uploads the output for you.
- **Option B — a Batch worker.** A small Python script that you submit to
  Google Batch. You write the download/upload yourself, which is more code but
  gives you full control.

Both options here do exactly the same thing: take a CRAM, read its first bytes,
and write a tiny report saying how big it is and whether it really starts with
the `CRAM` magic bytes. The analysis (`inspect_cram.py`) is intentionally
trivial so the focus stays on the two ways of running it. Swap that step for a
real tool (samtools, xTea, ...) once the pattern is clear.

---

## Which one should I use?

| | **dsub (Option A)** | **Worker + Batch (Option B)** |
|---|---|---|
| Lines of code you write | ~12 (one command) | ~120 (a Python worker) |
| Who moves data in/out of GCS | dsub | you |
| Requester-pays input (e.g. GP2) | works (`--user-project`) | works (you pass the billing project) |
| Control over the download | low | high (download part of a file, stream, branch, checkpoint) |
| Good for | most jobs, including heavy ones like xTea | jobs where the orchestration itself is the hard part |
| Compute cost | same (both are Google Batch underneath) | same |

**Start with dsub.** It is less code and covers the common case. Reach for a
worker only when you need control dsub does not give you (for example,
downloading just part of a large file to save I/O, custom checkpointing during
a long run, or branching logic on the input). The two are not "simple vs.
complex": dsub runs heavy pipelines too. The real difference is **how much of
the plumbing you write yourself**.

> Note: dsub is **not** a separate execution engine. With `--provider
> google-batch` it generates a Google Batch job, exactly like the worker's
> submit script does. Same VMs, same cost. (Older dsub used the now-retired
> Life Sciences API; that is why some guides say dsub "doesn't work with
> Batch". With a current dsub and `--provider google-batch`, it does.)

---

## What's in here

- `inspect_cram.py` — the shared analysis. Same logic for both options.
- `run_dsub.sh` — Option A: the dsub command, with blanks to fill in.
- `worker.py` — Option B: the worker (does GCS I/O by hand, then the same analysis).
- `submit_batch_job.py` — Option B: helper that submits `worker.py` to Batch.

---

## Prerequisites

You run all of this from a terminal **inside a Verily Workbench compute app**
(JupyterLab terminal, for example). The Workbench VM is already authenticated
as your workspace service account, so you do **not** run `gcloud auth login`.

Check your environment:

```bash
gcloud auth list           # should show a pet-...@<project>.iam.gserviceaccount.com as ACTIVE
gcloud config get-value project
```

Install dsub (Option A only):

```bash
pip install dsub
dsub --version             # 0.5.x is fine
```

---

## Finding the values you need

These are the values both options ask for. Finding them was the fiddly part,
so here is exactly where each comes from. (Values shown are examples; use
your own.)

**Project ID** — this is the *GCP project* underneath your workspace, which is
**not** the same as the workspace name. Get the real one:

```bash
wb workspace describe         # look at the "Google project:" line
# or, if it's already set:
gcloud config get-value project
```
Example: `wb-nice-fruitsalad-123`. (A name like
`my-project-gp2-gcp` is the *workspace*, not a project ID, and will
not work as `--project`.)

**Service account (the "pet" SA)** — already active on the VM:

```bash
gcloud auth list              # the ACTIVE pet-...@<project>.iam.gserviceaccount.com
```
Example: `pet-2771...@wb-nice-fruitsalad-123.iam.gserviceaccount.com`.

**Region** — your workspace's default location:

```bash
wb workspace describe         # "terra-default-location", e.g. europe-west4
```

**Network and subnetwork** — on Workbench these are conventionally named
`network` and `subnetwork`. Confirm:

```bash
gcloud compute networks list --project <PROJECT>
gcloud compute networks subnets list --filter="region:<REGION>" --project <PROJECT>
```
(These commands may print a red `VPC_SERVICE_CONTROLS` 403 line and then the
result anyway — the result is what matters.) Because Workbench VMs have no
external IP, jobs must use `--use-private-address`.

**A writable output bucket** — your workspace `ws_files` bucket, or another
bucket you can write to. It is mounted on the VM under `~/workspace/`.

**The input CRAM (GP2)** — GP2 CRAMs live in a **requester-pays** bucket, so
listing/reading them needs a billing project (`-u <PROJECT>`). The bucket is
also mounted on the VM, which is the easiest way to find the exact path:

```bash
# find the path on the mounted bucket
find /home/jupyter/workspace/gp2_crams -name "BBDP_000002*"
# confirm the gs:// path (note the -u for requester-pays)
gsutil -u <PROJECT> ls gs://gp2_crams/WGS/BBDP_000002/
```
Example path: `gs://gp2_crams/WGS/BBDP_000002/BBDP_000002.cram` (the `.crai`
index sits next to it). The CRAM lives in a per-sample subfolder, so the path
includes the sample ID twice.

**A container image** — because of `--use-private-address`, the image must come
from a Google-hosted registry (`gcr.io`, `*-docker.pkg.dev`, or
`mirror.gcr.io`). A public image like `quay.io/...` is rejected. These examples
use `mirror.gcr.io/library/python:3.11-slim` (Google's mirror of the official
Python image): it has Python, it is accepted by the private-address rule, and
the VPC can pull it.

---

## Option A — run with dsub

1. Put the analysis script where dsub can read it (a bucket you own):

   ```bash
   gsutil cp inspect_cram.py gs://<BUCKET>/scripts/inspect_cram.py
   ```

2. Edit the five values at the top of `run_dsub.sh`, then run it:

   ```bash
   bash run_dsub.sh
   ```

   It blocks until the job finishes (because of `--wait`). To submit and walk
   away instead, drop `--wait`; the job keeps running on Batch and you check it
   later with `dstat`.

3. Read the report:

   ```bash
   gsutil cat gs://<BUCKET>/output/<SAMPLE_ID>/report.txt
   ```

The whole command lives in `run_dsub.sh`. The key flags:
`--use-private-address` (Workbench VPC), `--user-project` (requester-pays
billing), `--image mirror.gcr.io/...` (Google-hosted), and `--input`/`--output`
(dsub does the GCS copy for you).

---

## Option B — run with a worker

1. Upload the worker to a bucket you own:

   ```bash
   gsutil cp worker.py gs://<BUCKET>/scripts/worker.py
   ```

2. Submit it to Batch with the helper script:

   ```bash
   python submit_batch_job.py \
     --project <PROJECT> \
     --region <REGION> \
     --sample-id BBDP_000002 \
     --image mirror.gcr.io/library/python:3.11-slim \
     --worker-script gs://<BUCKET>/scripts/worker.py \
     --env CRAM_PATH=gs://gp2_crams/WGS/BBDP_000002/BBDP_000002.cram \
     --env OUTPUT_BUCKET=<BUCKET> \
     --network network \
     --subnetwork subnetwork \
     --service-account pet-...@<PROJECT>.iam.gserviceaccount.com
   ```

   Add `--dry-run` to print the job JSON without submitting (useful to check
   the values first).

3. Watch it and read the result:

   ```bash
   gcloud batch jobs describe <JOB_NAME> \
     --project=<PROJECT> --location=<REGION> --format='value(status.state)'
   gsutil cat gs://<BUCKET>/output/BBDP_000002/report.txt
   ```

`worker.py` shows, in order, the four things dsub hides from you: get a token,
download the input (with a requester-pays retry), run the analysis, upload the
report.

---

## Reading the output

Both options write the same report. It looks like:

```
inspect_cram report
generated:  2026-06-25T05:59:16+00:00
sample_id:  BBDP_000002
input:      gs://gp2_crams/WGS/BBDP_000002/BBDP_000002.cram
size_bytes: 18472639201
first_bytes:b'CRAM'
is_cram:    True
```

`is_cram: True` means the worker really reached the file and read its bytes —
the end-to-end path worked.

---

## Common snags (all of which bit us)

- **`exit code 127` / "command not found"** — the image doesn't have the tool
  you called (e.g. the minimal samtools image has no `python`; the slim Python
  image has no `xxd`). Use an image that has what your command needs.
- **dsub: "must specify a --image with a gcr.io or pkg.dev host"** — you used a
  non-Google image with `--use-private-address`. Use `mirror.gcr.io/...` or push
  to your project's Artifact Registry.
- **"Bucket is a requester pays bucket but no user project provided"** — add
  `-u <PROJECT>` (gsutil), `--user-project <PROJECT>` (dsub), or the
  `userProject` retry (worker, already included).
- **"User project ... is invalid"** — you passed the workspace *name* instead
  of the GCP *project ID*. Use the `Google project:` value from
  `wb workspace describe`.
- **Empty logs / red VPC_SERVICE_CONTROLS 403 lines** — the perimeter blocks
  some read APIs; the command output usually still appears below the error.
  Job logs land in `gs://<BUCKET>/logs/` (look for `*-stderr.log`).
