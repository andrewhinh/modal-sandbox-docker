from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from dataclasses import dataclass

import modal


APP_NAME = "compose-preview-lab"
WORKDIR = "/workspace"
RUNC_VERSION = "v1.3.0"
RUNC_AMD64_SHA256 = "028986516ab5646370edce981df2d8e8a8d12188deaf837142a02097000ae2f2"

os.environ.setdefault("MODAL_IMAGE_BUILDER_VERSION", "2025.06")


START_DOCKERD_SH = """#!/bin/bash
set -xe -o pipefail

dev=$(ip route show default | awk '/default/ {print $5}')
if [ -z "$dev" ]; then
    echo "Error: No default device found."
    ip route show
    exit 1
fi

addr=$(ip addr show dev "$dev" | grep -w inet | awk '{print $2}' | cut -d/ -f1)
if [ -z "$addr" ]; then
    echo "Error: No IP address found for device $dev."
    ip addr show dev "$dev"
    exit 1
fi

echo 1 > /proc/sys/net/ipv4/ip_forward
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p tcp
iptables-legacy -t nat -A POSTROUTING -o "$dev" -j SNAT --to-source "$addr" -p udp

# gVisor does not support nftables yet, so use legacy iptables.
update-alternatives --set iptables /usr/sbin/iptables-legacy
update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy

exec /usr/bin/dockerd --iptables=false --ip6tables=false
"""


@dataclass(frozen=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


def docker_parent_image(start_dockerd_filename: str) -> modal.Image:
    return (
        modal.Image.from_registry("ubuntu:22.04")
        .env({"DEBIAN_FRONTEND": "noninteractive"})
        .apt_install(["wget", "ca-certificates", "curl", "net-tools", "iproute2"])
        .run_commands(
            [
                "install -m 0755 -d /etc/apt/keyrings",
                "curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc",
                "chmod a+r /etc/apt/keyrings/docker.asc",
                'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \\"${UBUNTU_CODENAME:-$VERSION_CODENAME}\\") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null',
                f"mkdir -p {WORKDIR}",
            ]
        )
        .apt_install(
            [
                "docker-ce=5:27.5.0-1~ubuntu.22.04~jammy",
                "docker-ce-cli=5:27.5.0-1~ubuntu.22.04~jammy",
                "containerd.io",
                "docker-buildx-plugin",
                "docker-compose-plugin",
            ]
        )
        .run_commands(
            [
                "rm $(which runc)",
                f"wget https://github.com/opencontainers/runc/releases/download/{RUNC_VERSION}/runc.amd64",
                f"echo '{RUNC_AMD64_SHA256}  runc.amd64' | sha256sum -c -",
                "chmod +x runc.amd64",
                "mv runc.amd64 /usr/local/bin/runc",
            ]
        )
        .run_commands(
            [
                "update-alternatives --set iptables /usr/sbin/iptables-legacy",
                "update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy",
            ]
        )
        .add_local_file(start_dockerd_filename, "/start-dockerd.sh", copy=True)
        .run_commands(["chmod +x /start-dockerd.sh"])
    )


def create_sandbox(
    app: modal.App,
    *,
    encrypted_ports: list[int],
    timeout_seconds: int = 60 * 60,
) -> modal.Sandbox:
    with tempfile.NamedTemporaryFile(mode="w", delete=True, encoding="utf-8") as script:
        script.write(START_DOCKERD_SH)
        script.flush()
        os.chmod(script.name, 0o755)
        return modal.Sandbox.create(
            "/start-dockerd.sh",
            app=app,
            image=docker_parent_image(script.name),
            encrypted_ports=encrypted_ports,
            timeout=timeout_seconds,
            experimental_options={"enable_docker": True},
        )


def _stream_lines(stream, *, label: str, name: str, output, chunks: list[str]) -> None:
    for line in stream:
        chunks.append(line)
        if output is not None:
            print(f"[{label}:{name}] {line}", end="", file=output)


def drain_process(process: modal.container_process.ContainerProcess, *, label: str, echo: bool) -> CommandResult:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_thread = threading.Thread(
        target=_stream_lines,
        args=(process.stdout,),
        kwargs={
            "label": label,
            "name": "stdout",
            "output": sys.stdout if echo else None,
            "chunks": stdout_chunks,
        },
    )
    stderr_thread = threading.Thread(
        target=_stream_lines,
        args=(process.stderr,),
        kwargs={
            "label": label,
            "name": "stderr",
            "output": sys.stderr if echo else None,
            "chunks": stderr_chunks,
        },
    )
    stdout_thread.start()
    stderr_thread.start()
    process.wait()
    stdout_thread.join()
    stderr_thread.join()
    return CommandResult("".join(stdout_chunks), "".join(stderr_chunks), process.returncode)


def run(
    sb: modal.Sandbox,
    *command: str,
    label: str,
    workdir: str | None = None,
    echo: bool = False,
    check: bool = True,
    timeout: int | None = None,
) -> CommandResult:
    result = drain_process(
        sb.exec(*command, workdir=workdir, timeout=timeout),
        label=label,
        echo=echo,
    )
    if check and result.returncode != 0:
        rendered = " ".join(command)
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}: {rendered}\n{result.stderr}"
        )
    return result


def wait_for_docker(sb: modal.Sandbox, timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = run(sb, "docker", "info", label="docker info", check=False)
        if result.returncode == 0:
            print("Docker daemon is ready.")
            return
        time.sleep(2)
    raise TimeoutError("Timed out waiting for dockerd to become ready.")
