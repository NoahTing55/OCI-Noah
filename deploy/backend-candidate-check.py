#!/usr/bin/env python3
"""Read-only local candidate API image evidence. No pull, run, restart or approval."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE = "ghcr.io/noahting55/oci-noah-api:sha-"


def inspect_candidate(sha: str, runner=subprocess.run) -> dict:
    if not SHA.fullmatch(sha):
        raise ValueError("Expected a full lowercase 40-character Git SHA")
    target = IMAGE + sha
    p = runner(
        ["docker", "image", "inspect", target, "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=15, check=True,
    )
    info = json.loads(p.stdout)
    labels = (info.get("Config") or {}).get("Labels") or {}
    names = info.get("RepoTags") or []
    if target not in names:
        raise RuntimeError("Inspected image does not have requested immutable SHA tag")
    digest = info.get("Id") or ""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError("Image digest missing or invalid")
    # OCI labels may be absent; report them rather than pretending verified.
    return {
        "candidate_image": target,
        "local_image_id": digest,
        "architecture": info.get("Architecture"),
        "os": info.get("Os"),
        "source_revision_label": labels.get("org.opencontainers.image.revision"),
        "tag_matches_requested_sha": True,
        "revision_label_verified": labels.get("org.opencontainers.image.revision") == sha,
        "candidate_compatibility_verified": False,
        "database_migration_verified": False,
        "release_authorized": False,
        "warning": "A SHA tag and locally present image do not establish image provenance or schema compatibility.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    try:
        result = inspect_candidate(args.sha)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"error":str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
