#!/usr/bin/env python3
"""Report-only admission check for a rendered external Guard candidate Compose JSON.

Never invokes Docker, reads .env, or authorizes production cutover.
Usage: docker compose -f <candidate.yml> config --format json |
       python deploy/single/validate-external-guard-rc.py
"""
import json
import re
import sys

DIGEST = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$")
ALLOWED_SERVICES = {"all-in-one", "docker-guard", "guard-socket-init"}

def validate(doc):
    errors = []
    if not isinstance(doc, dict):
        return ["not a Compose object"]
    services = doc.get("services")
    if not isinstance(services, dict) or set(services) != ALLOWED_SERVICES:
        return ["service set must be exactly all-in-one, docker-guard, guard-socket-init"]
    if doc.get("name") == "oci-nt":
        errors.append("active production Compose project name forbidden")
    for name in ("all-in-one", "docker-guard"):
        s = services[name]
        image = s.get("image", "")
        if not isinstance(image, str) or not DIGEST.fullmatch(image):
            errors.append(name + ": require immutable image @sha256 digest")
        if "build" in s:
            errors.append(name + ": no build allowed in candidate manifest")
        if s.get("privileged") or s.get("pid") == "host":
            errors.append(name + ": privileged/host pid forbidden")
        if "no-new-privileges:true" not in s.get("security_opt", []):
            errors.append(name + ": no-new-privileges required")
        if "ALL" not in s.get("cap_drop", []):
            errors.append(name + ": drop ALL capabilities")
    work = services["all-in-one"]
    guard = services["docker-guard"]
    init = services["guard-socket-init"]
    def mounts(s):
        return [m for m in s.get("volumes", []) if isinstance(m, dict)]
    wm, gm = mounts(work), mounts(guard)
    if any("docker.sock" in str(m.get("source", "")) or "docker.sock" in str(m.get("target", "")) for m in wm):
        errors.append("raw Docker socket in workload")
    if work.get("group_add"):
        errors.append("workload supplemental groups forbidden")
    if sorted(work.get("cap_add", [])) != ["KILL", "SETGID", "SETUID"]:
        errors.append("workload capabilities must be KILL/SETGID/SETUID only")
    if not any(m.get("target") == "/run/oci-guard" and m.get("read_only") is True for m in wm):
        errors.append("workload filtered socket mount must be read-only")
    if work.get("environment", {}).get("OCI_NT_EXTERNAL_GUARD") != "1":
        errors.append("external guard switch must be enabled")
    if work.get("environment", {}).get("DOCKER_GUARD_UNIX_SOCKET") != "/run/oci-guard/guard.sock":
        errors.append("workload must use filtered Unix transport")
    if guard.get("network_mode") != "none" or guard.get("privileged"):
        errors.append("Guard must have no network and not be privileged")
    if not any(m.get("target") == "/var/run/docker.sock" and m.get("read_only") is True for m in gm):
        errors.append("Guard alone must own raw Docker socket mount")
    if guard.get("restart") not in ("unless-stopped", "on-failure:3", "on-failure:5"):
        errors.append("Guard automatic restart policy required")
    if work.get("restart") != "unless-stopped":
        errors.append("workload automatic restart policy required")
    if init.get("restart") not in ("no", "none"):
        errors.append("one-shot init may not restart")
    for n, s in services.items():
        if s.get("network_mode") == "host" and n != "all-in-one":
            errors.append(n + ": host networking forbidden")
    return errors

def main():
    try:
        data=json.load(sys.stdin)
        errors=validate(data)
    except (ValueError, TypeError) as exc:
        errors=["invalid rendered Compose JSON: " + type(exc).__name__]
    print(json.dumps({"status":"BLOCKED" if errors else "CANDIDATE_MANIFEST_CONTRACT_OK_NOT_AUTHORIZED",
                      "errors":errors,"production_cutover_authorized":False},indent=2))
    return 2 if errors else 0

if __name__=="__main__":
    sys.exit(main())
