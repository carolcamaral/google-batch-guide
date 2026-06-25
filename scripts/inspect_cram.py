#!/usr/bin/env python3
"""
inspect_cram.py  —  the actual "analysis", shared by BOTH examples.

This is deliberately trivial so the focus stays on the two ways of running
it, not on the science. It reads the first bytes of a CRAM file and writes a
small text report. Swap the body of inspect() for a real tool (samtools,
xTea, ...) once you understand the pattern.

It reads its input and output paths from environment variables so that the
exact same file works under both dsub and the worker:

  - INPUT_PATH  : a LOCAL path to the already-downloaded CRAM
  - OUTPUT_PATH : a LOCAL path where the report should be written
  - SAMPLE_ID   : just a label for the report

Notice what is NOT here: any GCS download/upload code. Moving bytes in and
out of Cloud Storage is exactly the part that dsub and the worker each handle
differently. The analysis itself stays identical.
"""

import os
from datetime import datetime, timezone


def inspect(input_path):
    """Return a small dict of facts about the file. (Replace with real work.)"""
    size = os.path.getsize(input_path)
    with open(input_path, "rb") as f:
        magic = f.read(4)
    return {
        "size_bytes": size,
        "first_4_bytes": repr(magic),
        # Every CRAM file starts with the magic bytes b"CRAM".
        "looks_like_cram": magic == b"CRAM",
    }


def main():
    sample_id = os.environ.get("SAMPLE_ID", "SAMPLE")
    input_path = os.environ["INPUT_PATH"]
    output_path = os.environ["OUTPUT_PATH"]

    facts = inspect(input_path)

    report = (
        f"inspect_cram report\n"
        f"generated:  {datetime.now(timezone.utc).isoformat()}\n"
        f"sample_id:  {sample_id}\n"
        f"input:      {input_path}\n"
        f"size_bytes: {facts['size_bytes']}\n"
        f"first_bytes:{facts['first_4_bytes']}\n"
        f"is_cram:    {facts['looks_like_cram']}\n"
    )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)

    print(report, flush=True)


if __name__ == "__main__":
    main()
