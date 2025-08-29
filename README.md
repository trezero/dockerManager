# dockerManager (Python)

A robust Python CLI for maintaining Docker Desktop (WSL2) and WSL VHDXs on Windows. It mirrors and enhances your PowerShell tool with a modern UX (Typer + Rich) and safe operations.

## Features
- Show Docker usage (`docker system df -v`)
- Safe prune (builder + images)
- Prune ALL build cache
- Remove images with 0 containers (interactive)
- Compact VHDX targets (Docker data, Docker main, Ubuntu) with:
  - fstrim on relevant WSL distros (docker-desktop, docker-desktop-data, Ubuntu*)
  - optional zero-fill fallback
  - shutdown WSL and kill Docker Desktop before compaction
  - diskpart compact with before/after size reporting
- Register a monthly Scheduled Task to auto-compact

## Requirements
- Windows 10/11 with Docker Desktop (WSL2)
- Python 3.9+
- Run terminal as Administrator for compaction and task creation

## Quick start
```powershell
# From the dockerManager directory
./run.ps1 -- --help              # show CLI help
./run.ps1 menu                   # interactive menu
./run.ps1 usage                  # docker system df -v
./run.ps1 safe-prune             # prune builder+images
./run.ps1 prune-build-cache-all  # prune all build cache
./run.ps1 remove-unused-images   # interactively remove images with 0 containers
./run.ps1 compact --all          # compact detected VHDXs (admin)
./run.ps1 register-monthly-task  # create scheduled task (admin)
```

Notes:
- `run.ps1` bootstraps a `.venv`, installs `requirements.txt`, then runs the app.
- Pass any CLI args after `--` to avoid PowerShell parsing issues.

## Safety
Operations are conservative by default. Destructive actions prompt for confirmation unless `--yes` is provided.
