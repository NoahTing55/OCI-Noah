"""Minimal fail-fast supervisor for the experimental OCI-N&T all-in-one image.

A terminated service causes the entire container to exit, enabling Docker restart
policy to restart all services as a single unit. No detached daemon or orphan workers.
"""
import os
import pwd
import signal
import subprocess
import sys
import time

RUNNING = True
PROCESSES = []


def stop(_signum, _frame):
    global RUNNING
    RUNNING = False


def app_identity(socket_access=False):
    # The guard alone receives the host Docker socket's supplemental group.
    identity = pwd.getpwnam("guard" if socket_access else "app")
    def drop():
        if socket_access:
            gid = int(os.environ.get("DOCKER_GID", "0"))
            if gid <= 0:
                raise RuntimeError("DOCKER_GID must be the non-root host socket group")
            os.setgroups([gid])
        else:
            os.setgroups([])
        os.setgid(identity.pw_gid)
        os.setuid(identity.pw_uid)
    return drop


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    external_guard = os.environ.get("OCI_NT_EXTERNAL_GUARD", "0") == "1"
    if external_guard and not os.environ.get("DOCKER_GUARD_UNIX_SOCKET"):
        raise RuntimeError("external guard requires Unix socket transport")
    specs = [
        ("monitor", [sys.executable, "-m", "app.monitor_agent"], app_identity()),
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "9858", "--proxy-headers", "--forwarded-allow-ips=*"], app_identity()),
        ("web", ["nginx", "-g", "daemon off;"], None),
    ]
    if external_guard:
        # Nginx listens on unprivileged port 9859. Avoid root startup chown/capability needs.
        specs[-1] = ("web", ["nginx", "-g", "daemon off;"], app_identity())
    else:
        specs.insert(0, ("docker-guard", [sys.executable, "-m", "app.docker_socket_guard"], app_identity(socket_access=True)))
    try:
        for name, argv, identity in specs:
            proc = subprocess.Popen(argv, preexec_fn=identity, start_new_session=True)
            PROCESSES.append((name, proc))
            print(f"started {name} pid={proc.pid}", flush=True)
        while RUNNING:
            for name, proc in PROCESSES:
                rc = proc.poll()
                if rc is not None:
                    print(f"critical child {name} exited rc={rc}; stopping container", file=sys.stderr, flush=True)
                    return 1
            time.sleep(1)
        return 0
    finally:
        for _, proc in reversed(PROCESSES):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.monotonic() + 25
        for _, proc in reversed(PROCESSES):
            try:
                proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()


if __name__ == "__main__":
    sys.exit(main())
