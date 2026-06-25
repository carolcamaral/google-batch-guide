# xTea on Google Batch — Case Study

A complete, working example of scaling xTea (transposable element detection) on Google Batch via Verily Workbench.

This case study shows:
- How to prepare xTea inputs (reference files, sample setup)
- How to adapt the generic worker template for xTea
- How to submit jobs at scale (70+ samples)
- What to expect from outputs

**Status:** Validated on 27 May 2026. 50/71 TONIC-PERRON samples complete.

---

## Prerequisites

1. **xTea installed locally** in Workbench Jupyter (via conda)
2. **WGS data as CRAMs** in a GCS bucket (format: `gs://bucket/WGS/SAMPLE_ID/SAMPLE_ID.cram`)
3. **Reference files** ready (GRCh38 FASTA, gene annotations, TE libraries)
4. **Service account** with GCS + Batch permissions (as per main README)

---

## Step 1: Prepare reference files (one-time)

In Workbench Jupyter, download and organize reference files:

```bash
# Create directory structure
mkdir -p /home/jupyter/workspace/ws_files/ref
cd /home/jupyter/workspace/ws_files/ref

# Download GRCh38 (if not already present)
wget https://storage.googleapis.com/gcp-public-data--broad-references/hg38/v0/Homo_sapiens_assembly38.fasta
wget https://storage.googleapis.com/gcp-public-data--broad-references/hg38/v0/Homo_sapiens_assembly38.fasta.fai

# Index with bwa (if xTea expects .amb/.ann/etc)
bwa index Homo_sapiens_assembly38.fasta

# Download gene annotations
wget https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_human/release_45/gencode.v45.annotation.gff3.gz
gunzip gencode.v45.annotation.gff3.gz

# Download TE reference libraries (from xTea repo)
git clone https://github.com/parklab/xTea.git
cp -r xTea/reference_files/library/* ref/

# Verify structure
tree ref -L 2  # Should show Alu/, LINE/, SVA/, HERV/ subdirs
```

Then sync to GCS:

```bash
gsutil -m cp -r ref/ gs://<YOUR_BUCKET>/ref/
gsutil ls gs://<YOUR_BUCKET>/ref/ | head -20  # Verify
```

---

## Step 2: Generate xTea sample setup (one-time per cohort)

The xTea CLI (`xtea` command) requires a setup phase. Do this once per cohort in Workbench, then reuse the setup across all Batch jobs.

```bash
# In Workbench Jupyter
cd /home/jupyter/workspace/ws_files

# Create sample list (one per line)
cat > sample_list.txt <<EOF
TONIC_000001
TONIC_000002
TONIC_000003
...
TONIC_000071
EOF

# Create BAM list (maps sample to CRAM path)
# Note: for CRAMs, just point xTea at the CRAM; it will handle it like BAM
cat > bam_list.txt <<EOF
gs://gp2_crams/WGS/TONIC_000001/TONIC_000001.cram	illumina
gs://gp2_crams/WGS/TONIC_000002/TONIC_000002.cram	illumina
...
EOF

# Run xTea setup (generates scripts for all samples)
# Flags:
#   -y 15 = call Alu(2) + L1(1) + SVA(4) + HERV(8) = all four TE families
#   -n 8 = 8 threads (don't change; this is baked into job config)
#   -m 100 = assume 100 GB RAM available (match e2-highmem-16)
#   -f 5907 = all calling steps
python xTea/bin/xtea \
  -i sample_list.txt \
  -b bam_list.txt \
  -x null \
  -p /home/jupyter/workspace/ws_files/path_work_folder_tonic/ \
  -o submit_jobs_tonic.sh \
  -l /home/jupyter/workspace/ws_files/ref/ \
  -r /home/jupyter/workspace/ws_files/ref/Homo_sapiens_assembly38.fasta \
  -g /home/jupyter/workspace/ws_files/ref/gencode.v45.annotation.gff3 \
  --xtea /home/jupyter/workspace/ws_files/xTea/xtea/ \
  -f 5907 \
  -y 15 \
  -n 8 \
  -m 100

# This creates:
# - path_work_folder_tonic/TONIC_000001/L1/run_xTEA_pipeline.sh
# - path_work_folder_tonic/TONIC_000001/Alu/run_xTEA_pipeline.sh
# - ... (one per sample per TE type)
```

Verify output:

```bash
ls path_work_folder_tonic/TONIC_000001/
# Should show: L1/, Alu/, SVA/, HERV/

ls path_work_folder_tonic/TONIC_000001/Alu/
# Should show: run_xTEA_pipeline.sh, bam_list.txt, config files
```

Sync to GCS:

```bash
gsutil -m cp -r path_work_folder_tonic/ gs://<YOUR_BUCKET>/path_work_folder_tonic/
gsutil -m cp -r xTea/ gs://<YOUR_BUCKET>/xTea/
gsutil ls gs://<YOUR_BUCKET>/path_work_folder_tonic/TONIC_000001/Alu/
```

---

## Step 3: Adapt the worker for xTea

Copy `examples/worker_template.py` → `xtea_worker.py` and customize:

```python
# (See examples/xtea_case_study/xtea_worker.py for the full, working version)

# Key differences:
# 1. Download xTea source code from GCS
# 2. Patch BusyBox sort (sort -k1,1V → sort -k1,1)
# 3. Patch x_gvcf.py to remove -V flag
# 4. Download TE-specific reference files (varies by REPEAT_TYPE)
# 5. Download sample setup (run_xTEA_pipeline.sh, bam_list.txt)
# 6. Patch run_xTEA_pipeline.sh with local paths
# 7. Create tmp/ subdirectories (GCS doesn't sync empty dirs)
# 8. Run pipeline with checkpointing
# 9. Upload VCF + intermediates
```

Upload to GCS:

```bash
gsutil cp xtea_worker.py gs://<YOUR_BUCKET>/scripts/xtea_worker.py
```

---

## Step 4: Submit jobs

### Option A: Single sample, manual

```bash
python scripts/submit_batch_job.py \
  --project <YOUR_PROJECT_ID> \
  --region europe-west4 \
  --sample-id TONIC_000001 \
  --repeat-type Alu \
  --cram-path gs://gp2_crams/WGS/TONIC_000001/TONIC_000001.cram

# Output:
# ✓ Job submitted successfully: xtea-tonic-000001-alu-1624123456
#
# Monitoring commands:
#   gcloud batch jobs describe xtea-tonic-000001-alu-1624123456 ...
```

### Option B: Batch submission (all samples, all TE types)

```bash
# Submit 71 samples × 4 TE types = 284 jobs
for sample in TONIC_000001 TONIC_000002 ... TONIC_000071; do
  for te_type in Alu L1 SVA HERV; do
    python scripts/submit_batch_job.py \
      --sample-id "$sample" \
      --repeat-type "$te_type" &
    sleep 0.5  # Stagger
  done
done
wait

echo "Submitted 284 jobs"
```

### Option C: Monitor progress

```bash
# Count completed samples
completed=$(gsutil ls gs://<YOUR_BUCKET>/xtea_output/*/Alu/*.vcf 2>/dev/null | cut -d/ -f6 | sort -u | wc -l)
echo "Completed: $completed/71 samples"

# Watch in real-time
watch -n 10 'gsutil ls gs://<YOUR_BUCKET>/xtea_output/*/Alu/*.vcf 2>/dev/null | cut -d/ -f6 | sort -u | wc -l'
```

---

## Step 5: Validate outputs

### Check VCF files

```bash
# List outputs
gsutil ls gs://<YOUR_BUCKET>/xtea_output/TONIC_000001/*/

# Count variants per family
for te_type in Alu L1 SVA HERV; do
  vcf="gs://<YOUR_BUCKET>/xtea_output/TONIC_000001/${te_type}/*.vcf"
  count=$(gsutil cat "$vcf" 2>/dev/null | grep -vc "^#")
  echo "$te_type: $count variants"
done
```

Expected output for a healthy run:
```
Alu:  50–100 variants (high abundance)
L1:   20–50 variants
SVA:  5–20 variants
HERV: 0–5 variants (often 0)
```

### Download a sample VCF for inspection

```bash
gsutil cp gs://<YOUR_BUCKET>/xtea_output/TONIC_000001/Alu/*.vcf local_alu.vcf

# Check header + first 10 variants
head -30 local_alu.vcf

# Validate with bcftools
bcftools view local_alu.vcf | head
```

### Population merge (optional, next step)

Once all samples have individual VCFs, merge them:

```bash
# (Outside of Batch, in Workbench or local)
python xTea/xtea/x_vcf_merger.py -P \
  -i sample_info.txt \
  -x result_list.txt \
  -y 7 -w 35 \
  -p merge_output/ \
  -o merged_alu.vcf \
  -n 8
```

---

## Cost estimate

| Item | Cost | Notes |
|------|------|-------|
| **Setup (one-time)** | ~$5 | xTea prep in Workbench (maybe 30 min interactive) |
| **Per sample** | ~$1.44 | e2-highmem-16 @ $0.54/h × 3h average × 4 TE types in parallel |
| **71 samples** | ~$102 | 71 × $1.44 |
| **Storage (ref + output)** | ~$5 | ~250 GiB × $0.02/GiB/month |
| **Total (one-time cohort)** | ~$112 | Setup + compute + storage for full cohort |

---

## Troubleshooting xTea-specific issues

### Problem: Job shows SUCCEEDED but no Alu VCF

**Cause:** Specific CRAMs have abnormally high clipped-read counts, causing the re-alignment step to run for hours and time out.

**Check:** Look at the checkpoint logs:
```bash
gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/xtea_SAMPLE_*_Alu_*.txt | tail -1) | tail -50
```

Look for lines like:
```
[HH:MM:SS] Average coverage in cluster: 50
[HH:MM:SS] ... (repeats for hours)
[HH:MM:SS] Filter (on cns) cutoff: 2 and 4 are used
```

If it stops at "Filter (on cns)" and never reaches "Evaluating cascade" or VCF output, the job timed out during re-alignment.

**Solution:**
- Increase `maxRunDuration` to ~6 hours (21600s)
- Keep `-n 8` (parallelism helps)
- If it still times out, the CRAM may have an issue (contamination, high insertion diversity)

### Problem: Alu job takes 5+ hours

**Usual reason:** High clipped-read count in the CRAM (Alu is most abundant, processes first, hits the slow re-alignment).

**Check:**
```bash
# Count clipped reads in BAM locally
samtools view gs://gp2_crams/WGS/TONIC_000075/TONIC_000075.cram | \
  awk '{for(i=1;i<=NF;i++) if($i ~ /^S/||$i ~ /^H/) print}' | wc -l
```

If >10% of reads have clips, expect slow alignment.

**Mitigation:**
- Run Alu separately with longer timeout (4–6h)
- Run L1/SVA/HERV in parallel (they're faster)

### Problem: All jobs stuck on "Evaluating cascade layer"

**Cause:** This is a model-fitting step (ML classifier for TE candidates). It's normal for high-coverage samples.

**Action:** Wait. It can take 1–2 hours. If checkpoint logs show activity, let it run.

### Problem: SVLEN values are nonsense (millions of bp)

**Expected behavior for L1 transductions.** xTea encodes the offset to the source region, not insertion size. This is inherited from the caller, not introduced by Google Batch.

**When it matters:** If your downstream filter uses SVLEN. Workaround: filter by SUBTYPE instead:
```bash
# Exclude L1 transductions
bcftools view -e 'SUBTYPE="*transduction*"' input.vcf > output.vcf
```

---

## Key files

| File | Location | Purpose |
|------|----------|---------|
| **xtea_worker.py** | `examples/xtea_case_study/xtea_worker.py` | Full working xTea worker |
| **run_xTEA_pipeline.sh** | `path_work_folder_tonic/SAMPLE/TETYPE/` | Generated by xTea CLI; patched by worker |
| **xTea source** | `gs://BUCKET/xTea/xtea/` | Master branch from GitHub; has updated x_vcf_merger.py |
| **Reference files** | `gs://BUCKET/ref/` | Shared across all samples |
| **Sample setup** | `gs://BUCKET/path_work_folder_tonic/SAMPLE/TETYPE/` | Generated once per cohort; reused across Batch jobs |
| **Outputs** | `gs://BUCKET/xtea_output/SAMPLE/TETYPE/` | VCF + intermediates (delete intermediates to save space) |

---

## Performance tuning

### If jobs are too slow

1. **Keep `-n 8`** (xTea default; don't reduce)
2. **Increase maxRunDuration** to 6 hours (21600s)
3. **Increase memory** if OOM errors (e2-highmem-16 is already 128 GB; can't go higher on e2)
4. **Use CPU scheduling** (request specific core type, but not available in standard Batch)

### If costs are too high

1. **Consolidate TE types** — instead of 4 jobs per sample, try:
   ```bash
   # In xTea setup, use -y 15 (all TE types)
   # Then one worker handles all 4 at once
   # Jobs: 71 instead of 284
   ```
   (Requires testing RAM usage; likely ~150 GB for all 4 types together)

2. **Use spot VMs** — trade reliability for 70% cost savings:
   ```json
   // In job_config.json
   "provisioningModel": "SPOT"
   ```
   (Job can be preempted, but checkpoint logs are saved to GCS)

3. **Delete intermediates** after VCF is generated:
   ```bash
   # Keep only .vcf and .log
   gsutil -m rm gs://BUCKET/xtea_output/*/intermediate.*
   ```

---

## Next: Analysis

Once you have population-merged VCFs:

1. **Filter by annotation:**
   ```bash
   bcftools filter -i 'AF >= 0.01' merged_alu.vcf > common_alu.vcf
   ```

2. **Extract genotypes for PLINK:**
   ```bash
   bcftools query -f '%CHROM\t%POS\t%REF\t%ALT[\t%GT]\n' merged_alu.vcf > alu.raw
   ```

3. **Association analysis:**
   ```bash
   plink2 --vcf merged_alu.vcf --assoc --pheno pheno.txt
   ```

Or use R:
```r
vcf <- read.vcf("merged_alu.vcf")
genotypes <- extract.gt(vcf)
# GWAS, association, etc.
```

---

## References

- **xTea paper:** Chu C. et al., *Comprehensive identification of transposable element insertions using multiple sequencing technologies*, Nat Commun (2021)
- **xTea repo:** https://github.com/parklab/xTea
- **Container:** `quay.io/biocontainers/xtea:0.1.9--hdfd78af_0`

