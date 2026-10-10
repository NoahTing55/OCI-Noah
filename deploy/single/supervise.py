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


def app_identity():
    identity = pwd.getpwnam("app")
    def drop():
        os.setgroups([])
        os.setgid(identity.pw_gid)
        os.setuid(identity.pw_uid)
    return drop


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    specs = [
        ("docker-guard", [sys.executable, "-m", "app.docker_socket_guard"], app_identity()),
        ("monitor", [sys.executable, "-m", "app.monitor_agent"], app_identity()),
        ("api", [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "9858", "--proxy-headers", "--forwarded-allow-ips=*"], app_identity()),
        ("web", ["nginx", "-g", "daemon off;"], None),
    ]
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
