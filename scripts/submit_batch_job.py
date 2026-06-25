#!/usr/bin/env python3
"""
submit_batch_job.py

Generic helper script to submit Google Batch jobs without manually crafting JSON.
Works with any containerized workflow (xTea, GATK, bcftools, custom pipelines, etc).

Usage examples:

  # Basic: submit a job with default settings (samtools stats example)
  python submit_batch_job.py \
    --project my-project \
    --sample-id SAMPLE_001 \
    --image quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0 \
    --worker-script gs://my-bucket/scripts/worker.py

  # With custom environment variables (point at a specific CRAM)
  python submit_batch_job.py \
    --project my-project \
    --sample-id SAMPLE_001 \
    --image quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0 \
    --worker-script gs://my-bucket/scripts/worker.py \
    --env CRAM_PATH=gs://my-bucket/input/SAMPLE_001/SAMPLE_001.cram

  # With custom VM size and time
  python submit_batch_job.py \
    --project my-project \
    --sample-id SAMPLE_001 \
    --image gcr.io/custom/my-tool:latest \
    --worker-script gs://my-bucket/scripts/worker.py \
    --cpus 8 \
    --memory-gb 64 \
    --max-duration 7200

The script will:
  1. Generate a unique job name (no underscores)
  2. Create a JSON config with your parameters
  3. Submit to Google Batch
  4. Print monitoring commands
"""

import json
import subprocess
import sys
import argparse
import urllib.parse
from datetime import datetime


def sanitize_job_name(name):
    """Remove invalid characters from job name (Batch only accepts [a-z0-9-])."""
    name = name.replace("_", "-")
    name = "".join(c for c in name if c.isalnum() or c == "-")
    name = name.lower()
    return name


def create_batch_config(
    project_id,
    region,
    sample_id,
    image_uri,
    worker_script,
    env_vars=None,
    bucket_name=None,
    cpu_count=16,
    memory_gb=120,
    boot_disk_gb=200,
    max_duration_seconds=36000,
    service_account_email=None,
    network_name=None,
    subnetwork_name=None,
):
    """Generate a Google Batch job configuration (generic, tool-agnostic)."""
    
    if env_vars is None:
        env_vars = {}
    
    if bucket_name is None:
        # Try to infer from worker script path
        if worker_script.startswith("gs://"):
            bucket_name = worker_script[5:].split("/")[0]
        else:
            bucket_name = "<YOUR_BUCKET_NAME>"
    
    if service_account_email is None:
        service_account_email = "<YOUR_SERVICE_ACCOUNT_EMAIL>"
    
    if network_name is None:
        network_name = "<YOUR_NETWORK_NAME>"
    
    if subnetwork_name is None:
        subnetwork_name = "<YOUR_SUBNETWORK_NAME>"
    
    # Always include SAMPLE_ID and PROJECT
    env_vars = {
        "SAMPLE_ID": sample_id,
        "GOOGLE_CLOUD_PROJECT": project_id,
        **env_vars  # User-provided vars override defaults
    }
    
    # Worker download command (generic)
    # Build the GCS JSON-API "media download" URL correctly:
    #   gs://bucket/path/to/worker.py
    #     -> https://storage.googleapis.com/download/storage/v1/b/<bucket>/o/<url-encoded-object>?alt=media
    # The object path must be URL-encoded (slashes become %2F).
    if not worker_script.startswith("gs://"):
        print(f"ERROR: --worker-script must be a gs:// path, got: {worker_script}")
        sys.exit(1)
    _ws = worker_script[len("gs://"):]
    _ws_bucket, _ws_obj = _ws.split("/", 1)
    _encoded_obj = urllib.parse.quote(_ws_obj, safe="")
    worker_url = (
        f"https://storage.googleapis.com/download/storage/v1/b/"
        f"{_ws_bucket}/o/{_encoded_obj}?alt=media"
    )
    
    worker_download = f"""python -c "import urllib.request,urllib.parse,json; req=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',headers={{'Metadata-Flavor':'Google'}}); token=json.loads(urllib.request.urlopen(req,timeout=10).read())['access_token']; url='{worker_url}'; req2=urllib.request.Request(url,headers={{'Authorization':'Bearer '+token}}); open('/tmp/worker.py','wb').write(urllib.request.urlopen(req2,timeout=30).read())" && python /tmp/worker.py"""
    
    # Build config
    config = {
        "taskGroups": [
            {
                "taskSpec": {
                    "runnables": [
                        {
                            "container": {
                                "imageUri": image_uri,
                                "entrypoint": "/bin/bash",
                                "commands": ["-c", worker_download],
                            }
                        }
                    ],
                    "environment": {"variables": env_vars},
                    "computeResource": {
                        "cpuMilli": str(cpu_count * 1000),
                        "memoryMib": str(memory_gb * 1024),
                    },
                    "maxRetryCount": 0,
                    "maxRunDuration": f"{max_duration_seconds}s",
                },
                "taskCount": 1,
                "parallelism": 1,
            }
        ],
        "allocationPolicy": {
            "instances": [
                {
                    "policy": {
                        "machineType": "e2-highmem-16" if memory_gb >= 120 else "e2-highmem-8",
                        "bootDisk": {"sizeGb": boot_disk_gb},
                    }
                }
            ],
            "network": {
                "networkInterfaces": [
                    {
                        "network": f"projects/{project_id}/global/networks/{network_name}",
                        "subnetwork": f"projects/{project_id}/regions/{region}/subnetworks/{subnetwork_name}",
                        "noExternalIpAddress": True,
                    }
                ]
            },
            "location": {"allowedLocations": [f"regions/{region}"]},
            "serviceAccount": {"email": service_account_email},
        },
        "logsPolicy": {"destination": "CLOUD_LOGGING"},
    }
    
    return config


def submit_job(project_id, region, job_name, config):
    """Submit a job to Google Batch."""
    
    # Save config to temp file
    config_file = f"/tmp/batch_config_{job_name}.json"
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)
    
    # Submit
    cmd = [
        "gcloud",
        "batch",
        "jobs",
        "submit",
        job_name,
        f"--project={project_id}",
        f"--location={region}",
        f"--config={config_file}",
    ]
    
    print(f"Submitting job: {job_name}")
    print(f"  $ {' '.join(cmd)}")
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"\n✓ Job submitted successfully: {job_name}\n")
        print("Monitoring commands:")
        print(f"  # Check status")
        print(f"  gcloud batch jobs describe {job_name} \\")
        print(f"    --project={project_id} --location={region} \\")
        print(f"    --format='value(status.state)'")
        print()
        print(f"  # View logs (checkpoints every 10 min)")
        print(f"  gsutil cat $(gsutil ls gs://<YOUR_BUCKET>/logs/* | grep {job_name} | tail -1)")
        print()
        return 0
    else:
        print(f"\n✗ Failed to submit job:")
        print(result.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Submit a Google Batch job for any containerized workflow",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # samtools stats job (the simple built-in example)
  python submit_batch_job.py \\
    --project my-project \\
    --sample-id SAMPLE_001 \\
    --image quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0 \\
    --worker-script gs://my-bucket/scripts/worker.py \\
    --env CRAM_PATH=gs://my-bucket/input/SAMPLE_001/SAMPLE_001.cram

  # same, but also compute `samtools stats` (needs a reference FASTA)
  python submit_batch_job.py \\
    --project my-project \\
    --sample-id SAMPLE_001 \\
    --image quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0 \\
    --worker-script gs://my-bucket/scripts/worker.py \\
    --env CRAM_PATH=gs://my-bucket/input/SAMPLE_001/SAMPLE_001.cram \\
    --env REF_PATH=gs://my-bucket/ref/Homo_sapiens_assembly38.fasta
        """
    )
    
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--region", default="europe-west4", help="GCP region (default: europe-west4)")
    parser.add_argument("--sample-id", required=True, help="Sample identifier")
    parser.add_argument("--image", required=True, help="Container image URI (e.g., quay.io/biocontainers/samtools:1.19.2--h50ea8bc_0)")
    parser.add_argument("--worker-script", required=True, help="GCS path to worker script (e.g., gs://bucket/scripts/worker.py)")
    parser.add_argument("--env", action="append", default=[], help="Environment variable as KEY=VALUE (can be repeated)")
    parser.add_argument("--bucket", default=None, help="GCS bucket name (inferred from worker-script if not provided)")
    parser.add_argument("--cpus", type=int, default=16, help="Number of vCPUs (default: 16)")
    parser.add_argument("--memory-gb", type=int, default=120, help="RAM in GB (default: 120)")
    parser.add_argument("--disk-gb", type=int, default=200, help="Boot disk in GB (default: 200)")
    parser.add_argument("--max-duration", type=int, default=36000, help="Max run time in seconds (default: 36000 = 10h)")
    parser.add_argument("--service-account", default=None, help="Service account email")
    parser.add_argument("--network", default=None, help="VPC network name")
    parser.add_argument("--subnetwork", default=None, help="VPC subnetwork name")
    parser.add_argument("--dry-run", action="store_true", help="Print config without submitting")
    
    args = parser.parse_args()
    
    # Parse environment variables
    env_dict = {}
    for env_arg in args.env:
        if "=" not in env_arg:
            print(f"ERROR: Environment variable format must be KEY=VALUE, got: {env_arg}")
            sys.exit(1)
        key, value = env_arg.split("=", 1)
        env_dict[key] = value
    
    # Generate job name (generic, based on sample ID and timestamp)
    base_name = f"batch-{args.sample_id}-{int(datetime.now().timestamp())}"
    job_name = sanitize_job_name(base_name)
    
    # Create config
    config = create_batch_config(
        project_id=args.project,
        region=args.region,
        sample_id=args.sample_id,
        image_uri=args.image,
        worker_script=args.worker_script,
        env_vars=env_dict,
        bucket_name=args.bucket,
        cpu_count=args.cpus,
        memory_gb=args.memory_gb,
        boot_disk_gb=args.disk_gb,
        max_duration_seconds=args.max_duration,
        service_account_email=args.service_account,
        network_name=args.network,
        subnetwork_name=args.subnetwork,
    )
    
    # Print/submit
    if args.dry_run:
        print("Job configuration (dry-run):")
        print(json.dumps(config, indent=2))
        return 0
    else:
        return submit_job(args.project, args.region, job_name, config)


if __name__ == "__main__":
    sys.exit(main())
