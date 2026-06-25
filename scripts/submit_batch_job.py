#!/usr/bin/env python3
"""
submit_batch_job.py

Helper script to submit Google Batch jobs without manually crafting JSON.

Usage:
    python submit_batch_job.py \
      --project <YOUR_PROJECT_ID> \
      --region <YOUR_REGION> \
      --sample-id SAMPLE_001 \
      --cram-path gs://<YOUR_BUCKET>/input/SAMPLE_001/SAMPLE_001.cram \
      --repeat-type Alu

The script will:
  1. Generate a unique job name (no underscores)
  2. Create a JSON config with your parameters
  3. Submit to Google Batch
  4. Print the job name and monitoring commands
"""

import json
import subprocess
import sys
import argparse
from datetime import datetime
from pathlib import Path


def sanitize_job_name(name):
    """Remove underscores and ensure valid Batch job name."""
    name = name.replace("_", "-")
    name = "".join(c for c in name if c.isalnum() or c == "-")
    name = name.lower()
    return name


def create_batch_config(
    project_id,
    region,
    sample_id,
    cram_path,
    crai_path=None,
    repeat_type=None,
    bucket_name=None,
    cpu_count=16,
    memory_gb=120,
    boot_disk_gb=200,
    max_duration_seconds=36000,
    service_account_email=None,
    network_name=None,
    subnetwork_name=None,
):
    """Generate a Google Batch job configuration."""
    
    if crai_path is None:
        crai_path = cram_path + ".crai"
    
    if bucket_name is None:
        # Try to infer from CRAM path
        if cram_path.startswith("gs://"):
            bucket_name = cram_path[5:].split("/")[0]
        else:
            bucket_name = "<YOUR_BUCKET_NAME>"
    
    if service_account_email is None:
        service_account_email = f"<YOUR_SERVICE_ACCOUNT_EMAIL>"
    
    if network_name is None:
        network_name = "<YOUR_NETWORK_NAME>"
    
    if subnetwork_name is None:
        subnetwork_name = "<YOUR_SUBNETWORK_NAME>"
    
    # Environment variables
    env_vars = {
        "SAMPLE_ID": sample_id,
        "CRAM_PATH": cram_path,
        "CRAI_PATH": crai_path,
        "GOOGLE_CLOUD_PROJECT": project_id,
    }
    
    if repeat_type:
        env_vars["REPEAT_TYPE"] = repeat_type
    
    # Worker download command
    worker_url = f"https://storage.googleapis.com/download/storage/v1/b/{bucket_name}/o/scripts%2Fworker.py?alt=media"
    
    worker_download = f"""python -c "import urllib.request,urllib.parse,json; req=urllib.request.Request('http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',headers={{'Metadata-Flavor':'Google'}}); token=json.loads(urllib.request.urlopen(req,timeout=10).read())['access_token']; url='{worker_url}'; req2=urllib.request.Request(url,headers={{'Authorization':'Bearer '+token}}); open('/tmp/worker.py','wb').write(urllib.request.urlopen(req2,timeout=30).read())" && python /tmp/worker.py"""
    
    # Build config
    config = {
        "taskGroups": [
            {
                "taskSpec": {
                    "runnables": [
                        {
                            "container": {
                                "imageUri": "quay.io/biocontainers/xtea:0.1.9--hdfd78af_0",
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
        print(f"  gsutil cat $(gsutil ls gs://{config['allocationPolicy']['serviceAccount']['email'].split('@')[0]}*/logs/* | tail -1)")
        print()
        return 0
    else:
        print(f"\n✗ Failed to submit job:")
        print(result.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(
        description="Submit a Google Batch job for containerized workflows"
    )
    
    parser.add_argument("--project", required=True, help="Google Cloud project ID")
    parser.add_argument("--region", default="europe-west4", help="GCP region")
    parser.add_argument("--sample-id", required=True, help="Sample identifier")
    parser.add_argument("--cram-path", required=True, help="GCS path to CRAM file")
    parser.add_argument("--crai-path", default=None, help="GCS path to CRAM index (default: CRAM_PATH.crai)")
    parser.add_argument("--repeat-type", default=None, help="TE type (Alu, L1, SVA, HERV)")
    parser.add_argument("--bucket", default=None, help="GCS bucket name")
    parser.add_argument("--cpus", type=int, default=16, help="vCPUs (default: 16)")
    parser.add_argument("--memory-gb", type=int, default=120, help="RAM in GB (default: 120)")
    parser.add_argument("--disk-gb", type=int, default=200, help="Boot disk in GB (default: 200)")
    parser.add_argument("--max-duration", type=int, default=36000, help="Max run time in seconds (default: 36000 = 10h)")
    parser.add_argument("--service-account", default=None, help="Service account email")
    parser.add_argument("--network", default=None, help="VPC network name")
    parser.add_argument("--subnetwork", default=None, help="VPC subnetwork name")
    parser.add_argument("--dry-run", action="store_true", help="Print config without submitting")
    
    args = parser.parse_args()
    
    # Generate job name
    base_name = f"xtea-{args.sample_id}"
    if args.repeat_type:
        base_name += f"-{args.repeat_type}"
    base_name += f"-{int(datetime.now().timestamp())}"
    job_name = sanitize_job_name(base_name)
    
    # Create config
    config = create_batch_config(
        project_id=args.project,
        region=args.region,
        sample_id=args.sample_id,
        cram_path=args.cram_path,
        crai_path=args.crai_path,
        repeat_type=args.repeat_type,
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
