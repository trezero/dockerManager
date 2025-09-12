from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import typer
from rich.console import Console
from rich.table import Table
from rich.prompt import Confirm

console = Console()
app = typer.Typer(add_completion=False, no_args_is_help=True, help="Docker/WSL maintenance for Windows")

# ----------------------- Helpers -----------------------

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def require_admin() -> None:
    if not is_admin():
        console.print("[red]Administrator privileges required. Run PowerShell as Administrator.[/red]")
        raise typer.Exit(1)


def run_cmd(cmd: List[str], check: bool = True, capture: bool = False, shell: bool = False) -> subprocess.CompletedProcess:
    display = cmd if isinstance(cmd, list) else [str(cmd)]
    console.log("$ " + " ".join(display))
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, shell=shell)


def run_wsl(args: List[str], **kw) -> subprocess.CompletedProcess:
    return run_cmd(["wsl", *args], **kw)


def run_docker(args: List[str], **kw) -> subprocess.CompletedProcess:
    return run_cmd(["docker", *args], **kw)


@dataclass
class Paths:
    docker_data: Path
    docker_main: Path
    ubuntu: Optional[Path]


def detect_paths() -> Paths:
    local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    docker_data = local / "Docker" / "wsl" / "disk" / "docker_data.vhdx"
    docker_main = local / "Docker" / "wsl" / "data" / "ext4.vhdx"

    pkgs = local / "Packages"
    ubuntu_vhd: Optional[Path] = None
    if pkgs.exists():
        candidates = sorted(pkgs.glob("CanonicalGroupLimited.Ubuntu*"))
        for c in candidates:
            v = c / "LocalState" / "ext4.vhdx"
            if v.exists():
                ubuntu_vhd = v
                break

    return Paths(docker_data=docker_data, docker_main=docker_main, ubuntu=ubuntu_vhd)


# ----------------------- Docker Desktop management -----------------------

def find_docker_desktop_exe() -> Optional[Path]:
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Docker" / "Docker Desktop.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "Docker" / "Docker" / "Docker Desktop.exe",
    ]
    for p in candidates:
        if p and p.exists():
            return p
    return None


def is_docker_available() -> bool:
    try:
        cp = run_docker(["version", "--format", "{{.Server.Version}}"], check=False, capture=True)
        return cp.returncode == 0 and bool((cp.stdout or "").strip())
    except Exception:
        return False


def wait_for_docker(timeout: int = 120, interval: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_docker_available():
            return True
        time.sleep(interval)
    return False


def start_docker_desktop() -> bool:
    exe = find_docker_desktop_exe()
    if not exe:
        console.print("[yellow]Docker Desktop executable not found. Please start Docker Desktop manually.[/yellow]")
        return False
    console.print(f"[cyan]Starting Docker Desktop...[/cyan] {exe}")
    try:
        # Detach process so CLI continues
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen([str(exe)], creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
    except Exception as e:
        console.print(f"[red]Failed to launch Docker Desktop:[/red] {e}")
        return False
    return True


def ensure_docker_running() -> None:
    if is_docker_available():
        return
    console.print("[yellow]Docker engine not available. Attempting to start Docker Desktop...[/yellow]")
    if not start_docker_desktop():
        raise typer.Exit(1)
    if not wait_for_docker():
        console.print("[red]Docker engine did not become ready in time. Please verify Docker Desktop is running.[/red]")
        raise typer.Exit(1)


# ----------------------- Docker ops -----------------------

@app.command()
def usage() -> None:
    """Show Docker disk usage (docker system df -v)."""
    ensure_docker_running()
    try:
        run_docker(["system", "df", "-v"], check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Docker command failed:[/red] {e}")
        raise typer.Exit(1)


@app.command("safe-prune")
def safe_prune() -> None:
    """Safe prune for builder & images."""
    ensure_docker_running()
    steps = [
        ["builder", "prune", "-f"],
        ["image", "prune", "-f"],
        ["network", "prune", "-f"],
    ]
    for args in steps:
        try:
            run_docker(args, check=True)
        except subprocess.CalledProcessError as e:
            console.print(f"[yellow]Step failed:[/yellow] docker {' '.join(args)} -> {e}")


@app.command("prune-build-cache-all")
def prune_build_cache_all() -> None:
    """Prune ALL build cache."""
    ensure_docker_running()
    try:
        # builder prune -a is the closest to clearing all build cache
        run_docker(["builder", "prune", "-a", "-f"], check=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Docker command failed:[/red] {e}")
        raise typer.Exit(1)


@app.command("remove-unused-images")
def remove_unused_images(yes: bool = typer.Option(False, "--yes", help="Remove without prompting")) -> None:
    """Interactively remove images with 0 containers."""
    ensure_docker_running()
    try:
        cp = run_docker(["images", "--format", "{{.ID}};{{.Repository}};{{.Tag}};{{.Size}}"], check=True, capture=True)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to list images:[/red] {e}")
        raise typer.Exit(1)

    removed = 0
    for line in cp.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split(";")
        if len(parts) < 4:
            continue
        iid, repo, tag, size = parts[0].strip(), parts[1] or "<none>", parts[2] or "<none>", parts[3] or "?"
        try:
            used = run_docker(["ps", "-a", "--filter", f"ancestor={iid}", "-q"], check=True, capture=True)
        except subprocess.CalledProcessError:
            continue
        if used.stdout.strip():
            continue  # image has containers
        label = f"{repo}:{tag} ({iid}, {size})"
        if yes or Confirm.ask(f"Remove image with 0 containers: [b]{label}[/b]?", default=False):
            try:
                run_docker(["rmi", iid], check=True)
                removed += 1
            except subprocess.CalledProcessError as e:
                console.print(f"[yellow]Failed to remove {iid}:[/yellow] {e}")

    console.print(f"[green]Removed images:[/green] {removed}")


# ----------------------- WSL + diskpart -----------------------

KNOWN_TRIM_DISTROS = ["docker-desktop-data", "docker-desktop", "Ubuntu-22.04", "Ubuntu-24.04", "Ubuntu"]


def try_wsl_trim(distro: str) -> bool:
    try:
        run_wsl(["-d", distro, "bash", "-lc", "sudo fstrim -av || fstrim -av"], check=True)
        console.print(f"[green]fstrim OK:[/green] {distro}")
        return True
    except subprocess.CalledProcessError:
        console.print(f"[yellow]fstrim failed:[/yellow] {distro}")
        return False


def try_wsl_zero_fill(distro: str) -> bool:
    try:
        cmd = (
            "sudo sh -c 'dd if=/dev/zero of=/zero.fill bs=1M || true; sync; rm -f /zero.fill'"
        )
        run_wsl(["-d", distro, "bash", "-lc", cmd], check=True)
        console.print(f"[green]Zero-fill OK:[/green] {distro}")
        return True
    except subprocess.CalledProcessError:
        console.print(f"[yellow]Zero-fill failed:[/yellow] {distro}")
        return False


def kill_docker_and_shutdown_wsl() -> None:
    # Best-effort: try to close Docker Desktop and shutdown all WSL
    try:
        subprocess.run(["taskkill", "/IM", "Docker Desktop.exe", "/F"], check=False)
    except Exception:
        pass
    subprocess.run(["wsl", "--shutdown"], check=False)


def compact_vhd(vhd_path: Path) -> Tuple[int, int]:
    """Return (before_bytes, after_bytes)."""
    before = vhd_path.stat().st_size
    script = f"""
select vdisk file="{vhd_path}"
attach vdisk readonly
compact vdisk
detach vdisk
exit
""".strip()
    tmp = Path(os.environ.get("TEMP", str(Path.cwd()))) / "dp_compact.txt"
    tmp.write_text(script, encoding="utf-8")
    try:
        run_cmd(["diskpart", "/s", str(tmp)], check=True)
    finally:
        try:
            tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
    after = vhd_path.stat().st_size
    return before, after


@app.command()
def compact(
    docker_data: bool = typer.Option(False, "--docker-data", help="Compact Docker data VHDX"),
    docker_main: bool = typer.Option(False, "--docker-main", help="Compact Docker main VHDX (small)"),
    ubuntu: bool = typer.Option(False, "--ubuntu", help="Compact Ubuntu ext4.vhdx if detected"),
    all: bool = typer.Option(False, "--all", help="Compact all detected targets"),
    zero_fill: bool = typer.Option(False, "--zero-fill", help="Zero-fill fallback if fstrim fails"),
) -> None:
    """Compact selected VHDXs with TRIM/zero-fill prep and size report."""
    require_admin()

    paths = detect_paths()
    targets: List[Path] = []
    if all or docker_data:
        if paths.docker_data.exists():
            targets.append(paths.docker_data)
        else:
            console.print("[yellow]Docker data VHDX not found.[/yellow]")
    if all or docker_main:
        if paths.docker_main.exists():
            targets.append(paths.docker_main)
        else:
            console.print("[yellow]Docker main VHDX not found.[/yellow]")
    if (all or ubuntu) and paths.ubuntu and paths.ubuntu.exists():
        targets.append(paths.ubuntu)

    if not targets:
        console.print("[yellow]No targets selected/found.[/yellow]")
        raise typer.Exit(0)

    console.rule("Prepping filesystems (fstrim / zero-fill)")
    any_trim = False
    for d in KNOWN_TRIM_DISTROS:
        ok = try_wsl_trim(d)
        any_trim = any_trim or ok
        if not ok and zero_fill:
            try_wsl_zero_fill(d)

    console.rule("Stopping WSL and Docker Desktop")
    kill_docker_and_shutdown_wsl()

    console.rule("Compacting VHDX")
    table = Table(show_header=True, header_style="bold blue")
    table.add_column("VHDX")
    table.add_column("Before (GB)", justify="right")
    table.add_column("After (GB)", justify="right")

    for vhd in targets:
        try:
            b, a = compact_vhd(vhd)
            table.add_row(str(vhd), f"{b/ (1024**3):.2f}", f"{a/(1024**3):.2f}")
        except Exception as e:
            table.add_row(str(vhd), "-", f"error: {e}")

    console.print(table)


@app.command("register-monthly-task")
def register_monthly_task(day: int = typer.Option(1, min=1, max=28, help="Day of month to run (1-28)")) -> None:
    """Create a Windows Scheduled Task to compact monthly."""
    require_admin()
    root = Path(__file__).resolve().parents[1]  # dockerManager folder
    run_ps1 = root / "run.ps1"
    if not run_ps1.exists():
        console.print(f"[red]run.ps1 not found at {run_ps1}[/red]")
        raise typer.Exit(1)

    # Build task action: set LOCALAPPDATA for the user context then invoke run.ps1 as SYSTEM
    user_local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))
    action = (
        f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"$env:LOCALAPPDATA=\\\"{user_local}\\\"; & \\\"{run_ps1}\\\" compact --all\""
    )

    name = "Docker-WSL-Compact-Monthly"
    cmd = [
        "schtasks", "/Create",
        "/TN", name,
        "/SC", "MONTHLY",
        "/D", str(day),
        "/ST", "02:00",
        "/TR", action,
        "/RU", "SYSTEM",
        "/F",
    ]
    try:
        run_cmd(cmd, check=True)
        console.print(f"[green]Scheduled task created:[/green] {name}")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Failed to create scheduled task:[/red] {e}")
        raise typer.Exit(1)


# ----------------------- Menu -----------------------

@app.command()
def menu() -> None:
    paths = detect_paths()
    console.print("\n[b cyan]Detected VHDX paths[/b cyan]")
    mp = {
        "Docker data": paths.docker_data,
        "Docker main": paths.docker_main,
        "Ubuntu": paths.ubuntu or Path("<not found>"),
    }
    for k, v in mp.items():
        exists = v.exists() if isinstance(v, Path) else False
        console.print(f"- {k:12} [{'Y' if exists else 'N'}]  {v}")

    while True:
        console.print("\n[b]Menu[/b]")
        console.print("1) Show Docker disk usage")
        console.print("2) Safe prune (builder/images/network)")
        console.print("3) Prune ALL build cache")
        console.print("4) Remove images with 0 containers (interactive)")
        console.print("5) Compact VHDX (Docker data / Ubuntu)")
        console.print("6) Create monthly scheduled task")
        console.print("Q) Quit")
        choice = input("> ").strip().upper()
        if choice == "1":
            usage()
        elif choice == "2":
            safe_prune()
        elif choice == "3":
            prune_build_cache_all()
        elif choice == "4":
            remove_unused_images()
        elif choice == "5":
            compact(all=True)
        elif choice == "6":
            register_monthly_task()
        elif choice == "Q":
            break
        else:
            console.print("[yellow]Invalid option.[/yellow]")


if __name__ == "__main__":
    app()
    
@app.command()
def gui() -> None:
    """Launch the GUI dashboard (CustomTkinter)."""
    try:
        from .gui import run_gui
    except ImportError as e:
        console.print("[red]GUI dependencies missing. Run:[/red] pip install customtkinter")
        raise typer.Exit(1)
    run_gui()
