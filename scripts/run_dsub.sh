#!/usr/bin/env bash
#
# run_dsub.sh  —  OPTION A: run the analysis with dsub.
#
# dsub is declarative: you say which file comes IN, which script RUNS, and
# which file goes OUT. dsub downloads the input, runs the script, and uploads
# the output for you. You write no GCS code.
#
# Fill in the five values below for your environment (see the README for how
# to find each one), then: bash run_dsub.sh
#
set -euo pipefail

# ---- fill these in -----------------------------------------------------------
PROJECT="wb-nice-fruitsalad-1234"                 # your Workbench GCP project
REGION="europe-west4"                                # your workspace region
SA="pet-XXXX@${PROJECT}.iam.gserviceaccount.com"     # your pet service account
BUCKET="test-bucket-wb-nice-fruitsalad-1234"      # a bucket you can write to
CRAM="gs://gp2_crams/WGS/<SAMPLE_ID>/<SAMPLE_ID>.cram"  # the input CRAM
SAMPLE_ID="<SAMPLE_ID>"
# -----------------------------------------------------------------------------

# dsub turns each --input/--output into an environment variable inside the
# job (here: $CRAM and $OUT, holding the LOCAL paths dsub downloaded to /
# will upload from). We map those to the neutral names inspect_cram.py reads
# (INPUT_PATH / OUTPUT_PATH) so the *same* script works under both examples.
dsub \
  --provider google-batch \
  --project "${PROJECT}" \
  --location "${REGION}" --regions "${REGION}" \
  --logging "gs://${BUCKET}/logs" \
  --service-account "${SA}" \
  --network "projects/${PROJECT}/global/networks/network" \
  --subnetwork "projects/${PROJECT}/regions/${REGION}/subnetworks/subnetwork" \
  --use-private-address \
  --user-project "${PROJECT}" \
  --image mirror.gcr.io/library/python:3.11-slim \
  --input CRAM="${CRAM}" \
  --input SCRIPT="gs://${BUCKET}/scripts/inspect_cram.py" \
  --output OUT="gs://${BUCKET}/output/${SAMPLE_ID}/report.txt" \
  --env SAMPLE_ID="${SAMPLE_ID}" \
  --command 'export INPUT_PATH="${CRAM}"; export OUTPUT_PATH="${OUT}"; python "${SCRIPT}"' 

echo
echo "Done. Read the report with:"
echo "  gsutil cat gs://${BUCKET}/output/${SAMPLE_ID}/report.txt"
