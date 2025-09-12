from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from typing import Optional

import customtkinter as ctk

# Import internal helpers/ops from CLI module
from .app import (
    ensure_docker_running,
    is_admin,
    detect_paths,
    try_wsl_trim,
    try_wsl_zero_fill,
    kill_docker_and_shutdown_wsl,
    compact_vhd,
    run_docker,
    KNOWN_TRIM_DISTROS,
)


def run_gui():
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")

    app = ctk.CTk()
    app.title("Docker / WSL Manager")
    app.geometry("980x640")

    # Top status bar
    status_frame = ctk.CTkFrame(app)
    status_frame.pack(fill="x", padx=10, pady=(10, 5))

    admin_label = ctk.CTkLabel(status_frame, text=("Admin: YES" if is_admin() else "Admin: NO (run as Admin for compaction & scheduling)"))
    admin_label.pack(side="left", padx=8, pady=6)

    engine_status = ctk.CTkLabel(status_frame, text="Engine: checking…")
    engine_status.pack(side="left", padx=8)

    def set_engine_status(ok: bool):
        engine_status.configure(text=("Engine: Online" if ok else "Engine: Offline"))

    # Tabs
    tabs = ctk.CTkTabview(app)
    tabs.pack(fill="both", expand=True, padx=10, pady=5)

    tab_dashboard = tabs.add("Dashboard")
    tab_actions = tabs.add("Actions")
    tab_compact = tabs.add("Compact")
    tab_schedule = tabs.add("Schedule")

    # Shared log area
    log_box = ctk.CTkTextbox(app, height=180)
    log_box.pack(fill="both", padx=10, pady=(0, 10))

    def ui_log(msg: str):
        def _append():
            log_box.insert("end", msg + "\n")
            log_box.see("end")
        app.after(0, _append)

    def run_bg(fn, *, on_error: Optional[str] = None):
        def _runner():
            try:
                fn()
            except SystemExit:
                # Swallow Typer exits if any helper raises it
                if on_error:
                    ui_log(on_error)
            except Exception as e:
                ui_log(f"Error: {e}")
        threading.Thread(target=_runner, daemon=True).start()

    # ------------- Dashboard -------------
    dash_frame = ctk.CTkFrame(tab_dashboard)
    dash_frame.pack(fill="both", expand=True, padx=10, pady=10)

    paths = detect_paths()
    paths_table = ttk.Treeview(dash_frame, columns=("exists", "path"), show="headings", height=5)
    paths_table.heading("exists", text="Exists")
    paths_table.heading("path", text="Path")
    paths_table.column("exists", width=80, anchor="center")
    paths_table.column("path", width=700)
    paths_table.pack(fill="x", padx=8, pady=8)

    def refresh_paths():
        for i in paths_table.get_children():
            paths_table.delete(i)
        mp = {
            "Docker data": paths.docker_data,
            "Docker main": paths.docker_main,
            "Ubuntu": paths.ubuntu or Path("<not found>"),
        }
        for k, p in mp.items():
            exists = p.exists() if isinstance(p, Path) else False
            paths_table.insert("", "end", values=("Y" if exists else "N", str(p)))

    refresh_btn = ctk.CTkButton(dash_frame, text="Refresh", command=refresh_paths)
    refresh_btn.pack(side="left", padx=8, pady=6)

    def start_engine():
        def _start():
            try:
                ensure_docker_running()
                ui_log("Docker engine is online.")
                set_engine_status(True)
            except SystemExit:
                ui_log("Failed to start Docker Desktop. Please start it manually.")
                set_engine_status(False)
        run_bg(_start)

    start_btn = ctk.CTkButton(dash_frame, text="Start Docker Engine", command=start_engine)
    start_btn.pack(side="left", padx=8, pady=6)

    # ------------- Actions -------------
    act_frame = ctk.CTkFrame(tab_actions)
    act_frame.pack(fill="both", expand=True, padx=10, pady=10)

    def docker_usage():
        def _run():
            ensure_docker_running()
            cp = run_docker(["system", "df", "-v"], check=False, capture=True)
            ui_log(cp.stdout or "")
            if cp.returncode != 0:
                ui_log(cp.stderr or "docker system df failed")
        run_bg(_run)

    def safe_prune():
        def _run():
            ensure_docker_running()
            steps = [
                ["builder", "prune", "-f"],
                ["image", "prune", "-f"],
                ["network", "prune", "-f"],
            ]
            for s in steps:
                cp = run_docker(s, check=False, capture=True)
                ui_log(cp.stdout or "")
                if cp.returncode != 0:
                    ui_log(cp.stderr or f"docker {' '.join(s)} failed")
            ui_log("Safe prune complete.")
        run_bg(_run)

    def prune_cache_all():
        def _run():
            ensure_docker_running()
            cp = run_docker(["builder", "prune", "-a", "-f"], check=False, capture=True)
            ui_log(cp.stdout or "")
            if cp.returncode != 0:
                ui_log(cp.stderr or "builder prune -a failed")
        run_bg(_run)

    def remove_unused():
        def _run():
            ensure_docker_running()
            # List images and remove those with no containers referencing them
            list_cp = run_docker(["images", "--format", "{{.ID}};{{.Repository}};{{.Tag}};{{.Size}}"], check=False, capture=True)
            removed = 0
            for line in (list_cp.stdout or "").splitlines():
                parts = line.split(";")
                if len(parts) < 4:
                    continue
                iid = parts[0].strip()
                used = run_docker(["ps", "-a", "--filter", f"ancestor={iid}", "-q"], check=False, capture=True)
                if (used.stdout or "").strip():
                    continue
                rm = run_docker(["rmi", iid], check=False, capture=True)
                if rm.returncode == 0:
                    removed += 1
                    ui_log((rm.stdout or f"Removed {iid}").strip())
                else:
                    ui_log((rm.stderr or f"Failed to remove {iid}").strip())
            ui_log(f"Removed images: {removed}")
        if messagebox.askyesno("Confirm", "Remove all images with 0 containers?"):
            run_bg(_run)

    usage_btn = ctk.CTkButton(act_frame, text="Show Docker Usage", command=docker_usage)
    usage_btn.grid(row=0, column=0, padx=8, pady=8, sticky="w")

    safe_btn = ctk.CTkButton(act_frame, text="Safe Prune", command=safe_prune)
    safe_btn.grid(row=0, column=1, padx=8, pady=8, sticky="w")

    cache_btn = ctk.CTkButton(act_frame, text="Prune Build Cache (All)", command=prune_cache_all)
    cache_btn.grid(row=0, column=2, padx=8, pady=8, sticky="w")

    rm_btn = ctk.CTkButton(act_frame, text="Remove Unused Images", command=remove_unused)
    rm_btn.grid(row=0, column=3, padx=8, pady=8, sticky="w")

    # ------------- Compact -------------
    comp_frame = ctk.CTkFrame(tab_compact)
    comp_frame.pack(fill="both", expand=True, padx=10, pady=10)

    paths2 = detect_paths()
    var_docker_data = tk.BooleanVar(value=paths2.docker_data.exists())
    var_docker_main = tk.BooleanVar(value=paths2.docker_main.exists())
    var_ubuntu = tk.BooleanVar(value=bool(paths2.ubuntu and paths2.ubuntu.exists()))
    var_zero = tk.BooleanVar(value=False)

    c1 = ctk.CTkCheckBox(comp_frame, text=f"Docker data: {paths2.docker_data}", variable=var_docker_data)
    c2 = ctk.CTkCheckBox(comp_frame, text=f"Docker main: {paths2.docker_main}", variable=var_docker_main)
    c3 = ctk.CTkCheckBox(comp_frame, text=f"Ubuntu: {paths2.ubuntu}", variable=var_ubuntu)
    c4 = ctk.CTkCheckBox(comp_frame, text="Zero-fill fallback if TRIM fails", variable=var_zero)

    c1.pack(anchor="w", padx=8, pady=4)
    c2.pack(anchor="w", padx=8, pady=4)
    c3.pack(anchor="w", padx=8, pady=4)
    c4.pack(anchor="w", padx=8, pady=8)

    def do_compact():
        if not is_admin():
            messagebox.showwarning("Admin required", "Run PowerShell as Administrator to compact VHDX files.")
            return

        def _run():
            ui_log("Prepping filesystems (fstrim / zero-fill)…")
            for d in KNOWN_TRIM_DISTROS:
                ok = try_wsl_trim(d)
                ui_log(f"TRIM {d}: {'OK' if ok else 'FAILED'}")
                if not ok and var_zero.get():
                    z = try_wsl_zero_fill(d)
                    ui_log(f"Zero-fill {d}: {'OK' if z else 'FAILED'}")

            ui_log("Stopping WSL and Docker Desktop…")
            kill_docker_and_shutdown_wsl()

            targets = []
            if var_docker_data.get() and paths2.docker_data.exists():
                targets.append(paths2.docker_data)
            if var_docker_main.get() and paths2.docker_main.exists():
                targets.append(paths2.docker_main)
            if var_ubuntu.get() and paths2.ubuntu and paths2.ubuntu.exists():
                targets.append(paths2.ubuntu)

            if not targets:
                ui_log("No targets selected or found.")
                return

            for v in targets:
                try:
                    b, a = compact_vhd(v)
                    ui_log(f"{v}: {b/(1024**3):.2f} GB -> {a/(1024**3):.2f} GB")
                except Exception as e:
                    ui_log(f"{v}: error: {e}")
            ui_log("Compaction complete.")

        run_bg(_run)

    compact_btn = ctk.CTkButton(comp_frame, text="Run Compact", command=do_compact)
    compact_btn.pack(padx=8, pady=10)

    # ------------- Schedule -------------
    sch_frame = ctk.CTkFrame(tab_schedule)
    sch_frame.pack(fill="both", expand=True, padx=10, pady=10)

    ctk.CTkLabel(sch_frame, text="Day of month (1-28)").pack(anchor="w", padx=8)
    day_spin = ttk.Spinbox(sch_frame, from_=1, to=28, width=6)
    day_spin.set("1")
    day_spin.pack(anchor="w", padx=8, pady=6)

    def create_task():
        if not is_admin():
            messagebox.showwarning("Admin required", "Run PowerShell as Administrator to create a Scheduled Task.")
            return
        try:
            from .app import register_monthly_task
            d = int(day_spin.get())
            def _run():
                register_monthly_task(day=d)
            run_bg(_run)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    sch_btn = ctk.CTkButton(sch_frame, text="Create Monthly Task", command=create_task)
    sch_btn.pack(padx=8, pady=8)

    # Initial engine status check in background
    def _check_engine():
        try:
            ensure_docker_running()
            set_engine_status(True)
            ui_log("Docker engine is online.")
        except SystemExit:
            set_engine_status(False)
            ui_log("Docker engine offline. Start it from Dashboard tab.")
    run_bg(_check_engine)

    app.mainloop()
