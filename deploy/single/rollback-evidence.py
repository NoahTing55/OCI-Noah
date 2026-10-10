#!/usr/bin/env python3
"""Produce a restricted, secret-free evidence manifest from a cutover PLAN.

This tool NEVER stops, creates, renames or updates containers, and NEVER copies
production credentials or DB. It provides a fixed input for human-reviewed
cutover/rollback planning, not release authorization.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location

CONTROLLER = Path(__file__).with_name("release-controller.py")


def controller():
    spec = spec_from_file_location("oci_single_release_controller", CONTROLLER)
    mod = module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


def safe_manifest(report: dict) -> dict:
    names = {"oci-nt-api", "oci-nt-web", "oci-nt-monitor-agent", "oci-nt-docker-guard"}
    if report.get("mode") != "PLAN_ONLY" or report.get("release_authorized") is not False:
        raise ValueError("unsafe release report")
    images = report.get("original_images")
    if not isinstance(images, dict) or set(images) != names:
        raise ValueError("incomplete original image inventory")
    if not all(isinstance(v, str) and re.fullmatch(r"ghcr\.io/noahting55/oci-noah-(?:api|web):sha-[0-9a-f]{40}", v) for v in images.values()):
        raise ValueError("refusing non-pinned/foreign original image")
    image = report.get("single_image")
    if not isinstance(image, str) or not re.fullmatch(r"ghcr\.io/noahting55/oci-noah-single:sha-[0-9a-f]{40}", image):
        raise ValueError("invalid single candidate")
    if report.get("schema") != 7:
        raise ValueError("schema mismatch")
    return {
        "manifest_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "release_authorized": False,
        "candidate_image": image,
        "candidate_image_id": report["single_image_id"],
        "original_four_images": {name:images[name] for name in sorted(names)},
        "database_schema": 7,
        "docker_socket_gid": report["docker_socket_gid"],
        "separate_project_excluded": "/opt/oci--nt",
        "contains_database_or_secrets": False,
        "evidence_scope": "configuration inventory only; no runtime quiescence or rollback proof",
        "manual_review_required": True,
    }


def write_exclusive(path: Path, value: dict):
    if path.is_symlink() or path.exists():
        raise FileExistsError("refusing to overwrite preexisting evidence")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("output directory must already exist and must not be a symlink")
    payload = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    old_umask = os.umask(0o077)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        os.umask(old_umask)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = controller().inspect(args.sha)
        manifest = safe_manifest(report)
        write_exclusive(args.out, manifest)
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status":"BLOCKED","release_authorized":False,"reason":type(exc).__name__}))
        return 3
    print(json.dumps({"status":"EVIDENCE_WRITTEN","path":str(args.out),
                      "release_authorized":False},ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
