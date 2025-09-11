#!/usr/bin/env python3
"""
Hardware Stress Testing Tool (Pro Edition)

App Name: Hardware Stress Testing Tool
Version: 2.0 GUI
Ported frm C++
Revision Date: 2025-09-06
Author: Dr. Eric O. Flores
"""

import os
import time
import shlex
import shutil
import queue
import platform
import datetime
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional dependency for dashboard
try:
    import psutil
    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

APP_NAME = "Hardware Stress Testing Tool"
VERSION = "2.0"
REVISION_DATE = "2025-09-06"
AUTHOR = "Dr. Eric O. Flores"

LOG_DIR = os.path.join(os.path.expanduser("~"), "HardwareStressTest", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

REQUIRED_CMDS = {
    "cpu": ["stress-ng"],
    "ram": ["stress-ng"],
    "gpu": ["glmark2"],
    "disk": ["fio"],
    "net": ["iperf3"],
}

def which_or_hint(cmd: str) -> str:
    path = shutil.which(cmd)
    if path:
        return path
    hint_map = {
        "stress-ng": "sudo apt install stress-ng",
        "glmark2": "sudo apt install glmark2",
        "fio": "sudo apt install fio",
        "iperf3": "sudo apt install iperf3",
    }
    hint = hint_map.get(cmd, f"Please install '{cmd}'.")
    return f"NOT FOUND ({hint})"

def timestamp() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# -------------------------
# DonutGauge (labels above arc; compact)
# -------------------------

class DonutGauge(tk.Canvas):
    """
    Compact semicircle gauge:
      - Label is drawn in a reserved band ABOVE the arc (won’t be clipped).
      - Center % and caption below.
      - Text is black for maximum legibility.
      - Start angle 280°, extent scales with value.
    """
    def __init__(self, master, width=200, height=160, **kw):
        # FIXED: Provide a safe default 'bg' color instead of reading from ttk parent.
        # The .cget("background") call fails on modern ttk widgets.
        bg = kw.pop("bg", "#EFEFEF")
        super().__init__(master, width=width, height=height, highlightthickness=0, bg=bg, **kw)

        self.w = width
        self.h = height
        self.pad = 10
        self.label_band = 22         # <<< reserved space for label at the top
        self.arc_width = 14

        # Colors
        self.track_color = "#c7ced6"
        self.arc_color = "#84cc16"
        self.text_color = "#000000"   # all texts black
        self.caption_color = "#000000"
        self.caption_color_coded = False

        # Geometry: arc is moved down below the label band
        d = min(self.w, self.h*2) - self.pad*2
        top = self.pad + self.label_band                          # <<< push arc down
        self.bbox = (self.pad, top, self.pad + d, top + d)
        self.cx = self.w // 2
        self.cy = int(top + d*0.55)                               # center text position

        # Elements
        self.id_lbl = self.create_text(self.cx, self.pad + 10, text="",  # <<< label clearly above arc
                                       fill=self.text_color, font=("TkDefaultFont", 10, "bold"))
        self.id_track = self.create_arc(self.bbox, start=280, extent=280, style="arc",
                                        width=self.arc_width, outline=self.track_color)
        self.id_arc = self.create_arc(self.bbox, start=280, extent=0, style="arc",
                                      width=self.arc_width, outline=self.arc_color)
        self.id_val = self.create_text(self.cx, self.cy, text="", fill=self.text_color,
                                       font=("TkDefaultFont", 12, "bold"))
        self.id_cap = self.create_text(self.cx, self.h - 12, text="", fill=self.caption_color,
                                       font=("TkDefaultFont", 10))

        self.value = 0.0

    # Public API
    def set_label(self, text: str):
        self.itemconfigure(self.id_lbl, text=text)

    def set_colors(self, arc_color: str):
        self.arc_color = arc_color
        self.itemconfigure(self.id_arc, outline=self.arc_color)
        self._refresh_caption_color()

    def set(self, value: float, caption: str):
        self.value = max(0.0, min(1.0, float(value)))
        self.itemconfigure(self.id_arc, extent=280.0 * self.value)
        self.itemconfigure(self.id_val, text=f"{int(round(self.value*100))}%")
        self.itemconfigure(self.id_cap, text=caption)

    def set_theme(self, *, bg="#EFEFEF", track="#c7ced6", text="#000000",
                  caption="#000000", color_code_caption=False):
        self.configure(bg=bg)
        self.track_color = track
        self.caption_color = caption
        self.caption_color_coded = bool(color_code_caption)
        self.itemconfigure(self.id_track, outline=self.track_color)
        self.itemconfigure(self.id_lbl, fill=text)
        self.itemconfigure(self.id_val, fill=text)
        self.itemconfigure(self.id_cap, fill=(self.arc_color if self.caption_color_coded else self.caption_color))

    def _refresh_caption_color(self):
        self.itemconfigure(self.id_cap, fill=(self.arc_color if self.caption_color_coded else self.caption_color))

# -------------------------
# Background command runner
# -------------------------

class CommandRunner:
    def __init__(self, on_line, on_done):
        self.on_line = on_line
        self.on_done = on_done
        self.process: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self._stop_flag = threading.Event()

    def start(self, command, cwd=None, shell=False, env=None):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("A command is already running.")
        self._stop_flag.clear()
        self.thread = threading.Thread(target=self._run, args=(command, cwd, shell, env), daemon=True)
        self.thread.start()

    def _run(self, command, cwd, shell, env):
        try:
            popen_cmd = command if (shell or isinstance(command, (list, tuple))) else shlex.split(command)
            self.process = subprocess.Popen(
                popen_cmd, cwd=cwd, shell=shell, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True,
            )
            for line in self.process.stdout:  # type: ignore
                if self._stop_flag.is_set():
                    break
                self.on_line(line)

            if self._stop_flag.is_set() and self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                except Exception:
                    pass

            try:
                rc = self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                rc = self.process.returncode
            self.on_done(rc)
        except Exception as e:
            self.on_line(f"\nException: {e}\n")
            self.on_done(-1)
        finally:
            self.process = None

    def stop(self):
        self._stop_flag.set()

    def is_running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

# -------------------------
# Main Application (Tk) – uses GRID for robust sizing
# -------------------------

class StressTestApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("900x620")        # <<< tighter default size
        self.minsize(820, 540)          # <<< sensible minimum

        # theme state
        self.theme = tk.StringVar(value="light")
        self.caption_colored = tk.BooleanVar(value=False)

        # runtime state
        self.runner = CommandRunner(self._on_line, self._on_done)
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.current_log_path: str | None = None
        self.log_fp = None
        self.test_start_time: float | None = None
        self.expected_duration: int | None = None
        self._ui_updater_running = False
        self.status_var = tk.StringVar(value="Ready.")

        # ---------- GRID LAYOUT ROOT ----------
        # 0: top controls, 1: dashboard (fixed), 2: output (expands), 3: status bar
        self.rowconfigure(0, weight=0)
        self.rowconfigure(1, weight=0)
        self.rowconfigure(2, weight=1)  # <<< output stretches
        self.rowconfigure(3, weight=0)
        self.columnconfigure(0, weight=1)

        self._build_menu()
        self._build_top_panel()     # row 0
        self._build_dashboard()     # row 1
        self._build_output_panel()  # row 2
        self._build_statusbar()     # row 3

        self._apply_theme()
        self._check_dependencies_summary()
        self.after(100, self._drain_output_queue)

    # ---------- UI builders ----------

    def _build_menu(self):
        menubar = tk.Menu(self)
        # File
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Save Output As…", command=self._save_output_as)
        file_menu.add_command(label="Open Log Folder", command=self._open_log_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_exit)
        menubar.add_cascade(label="File", menu=file_menu)
        # Tools
        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Check Dependencies", command=self._check_dependencies_dialog)
        theme_menu = tk.Menu(tools_menu, tearoff=0)
        theme_menu.add_radiobutton(label="Light Mode", variable=self.theme, value="light", command=self._apply_theme)
        theme_menu.add_radiobutton(label="Dark Mode",  variable=self.theme, value="dark",  command=self._apply_theme)
        theme_menu.add_separator()
        theme_menu.add_checkbutton(label="Color-code gauge captions",
                                   variable=self.caption_colored, command=self._apply_caption_color_mode)
        tools_menu.add_cascade(label="Theme", menu=theme_menu)
        menubar.add_cascade(label="Tools", menu=tools_menu)
        # Help
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _build_top_panel(self):
        top = ttk.Frame(self, padding=(10, 10, 10, 0))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)

        test_row = ttk.Frame(top)
        test_row.grid(row=0, column=0, sticky="ew")
        self.test_var = tk.StringVar(value="cpu")
        ttk.Label(test_row, text="Select Test:").pack(side=tk.LEFT, padx=(0, 8))
        for val, txt in [("cpu", "CPU"), ("ram", "RAM"), ("gpu", "GPU"), ("disk", "Disk"), ("net", "Network")]:
            ttk.Radiobutton(test_row, text=txt, value=val, variable=self.test_var,
                            command=self._on_test_change).pack(side=tk.LEFT, padx=4)

        self.options_container = ttk.Frame(top)
        self.options_container.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self._build_cpu_opts(); self._build_ram_opts(); self._build_gpu_opts()
        self._build_disk_opts(); self._build_net_opts()
        self._show_only_options("cpu")

        ctrl = ttk.Frame(top)
        ctrl.grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.start_btn = ttk.Button(ctrl, text="Start", command=self._start_clicked)
        self.stop_btn = ttk.Button(ctrl, text="Stop", command=self._stop_clicked, state=tk.DISABLED)
        self.clear_btn = ttk.Button(ctrl, text="Clear Output", command=self._clear_output)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        self.clear_btn.pack(side=tk.LEFT, padx=6)

        prog = ttk.Frame(top)
        prog.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(prog, text="Progress:").pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(prog, orient=tk.HORIZONTAL, mode="determinate", length=400)
        self.progress.pack(side=tk.LEFT, padx=8)
        self.eta_label = ttk.Label(prog, text="ETA: --:--")
        self.eta_label.pack(side=tk.LEFT, padx=8)

    def _build_dashboard(self):
        dash = ttk.Frame(self, padding=(10, 6, 10, 0))
        dash.grid(row=1, column=0, sticky="ew")
        # center gauges without overflow
        left = ttk.Frame(dash); mid = ttk.Frame(dash); right = ttk.Frame(dash)
        left.pack(side=tk.LEFT, expand=True); mid.pack(side=tk.LEFT, expand=True); right.pack(side=tk.LEFT, expand=True)

        self.gauge_cpu = DonutGauge(left,  width=200, height=160)
        self.gauge_mem = DonutGauge(mid,   width=200, height=160)
        self.gauge_dsk = DonutGauge(right, width=200, height=160)

        self.gauge_cpu.set_label("CPU")
        self.gauge_cpu.set_colors("#84cc16")  # green
        self.gauge_mem.set_label("MEMORY")
        self.gauge_mem.set_colors("#f59e0b")  # amber
        self.gauge_dsk.set_label("DISK")
        self.gauge_dsk.set_colors("#e11d48")  # rose

        self.gauge_cpu.pack(padx=4, pady=2)
        self.gauge_mem.pack(padx=4, pady=2)
        self.gauge_dsk.pack(padx=4, pady=2)

        if not HAS_PSUTIL:
            self.gauge_cpu.set(0.0, "psutil not installed")
            self.gauge_mem.set(0.0, "psutil not installed")
            self.gauge_dsk.set(0.0, "psutil not installed")
            self.status_var.set("Dashboard requires 'psutil' (pip install psutil).")
        else:
            self.after(300, self._update_dashboard)

    def _build_output_panel(self):
        mid = ttk.Frame(self, padding=(10, 8, 10, 10))
        mid.grid(row=2, column=0, sticky="nsew")     # <<< expands with window
        mid.rowconfigure(0, weight=1)
        mid.columnconfigure(0, weight=1)

        self.output_text = tk.Text(mid, wrap=tk.WORD)
        yscroll = ttk.Scrollbar(mid, orient=tk.VERTICAL, command=self.output_text.yview)
        self.output_text.configure(yscrollcommand=yscroll.set)

        self.output_text.grid(row=0, column=0, sticky="nsew")     # <<< takes remaining space
        yscroll.grid(row=0, column=1, sticky="ns")

    def _build_statusbar(self):
        bottom = ttk.Frame(self, padding=(10, 0, 10, 10))
        bottom.grid(row=3, column=0, sticky="ew")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

    # ---------- Options panes ----------

    def _build_cpu_opts(self):
        f = self.cpu_opts = ttk.LabelFrame(self.options_container, text="CPU Options")
        self.cpu_workers = tk.IntVar(value=max(1, os.cpu_count() or 1))
        self.cpu_timeout = tk.IntVar(value=300)
        ttk.Label(f, text="Workers:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Spinbox(f, from_=1, to=512, textvariable=self.cpu_workers, width=7).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Duration (s):").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        ttk.Spinbox(f, from_=5, to=86400, textvariable=self.cpu_timeout, width=7).grid(row=0, column=3, sticky="w", padx=4, pady=4)

    def _build_ram_opts(self):
        f = self.ram_opts = ttk.LabelFrame(self.options_container, text="RAM Options")
        self.ram_vm_workers = tk.IntVar(value=2)
        self.ram_bytes = tk.StringVar(value="1G")
        self.ram_timeout = tk.IntVar(value=300)
        ttk.Label(f, text="VM Workers:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Spinbox(f, from_=1, to=512, textvariable=self.ram_vm_workers, width=7).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Bytes per VM:").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        ttk.Entry(f, textvariable=self.ram_bytes, width=10).grid(row=0, column=3, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Duration (s):").grid(row=0, column=4, sticky="w", padx=4, pady=4)
        ttk.Spinbox(f, from_=5, to=86400, textvariable=self.ram_timeout, width=7).grid(row=0, column=5, sticky="w", padx=4, pady=4)

    def _build_gpu_opts(self):
        f = self.gpu_opts = ttk.LabelFrame(self.options_container, text="GPU Options")
        ttk.Label(f, text="glmark2 runs a fixed suite and exits (no duration setting).").grid(row=0, column=0, sticky="w", padx=4, pady=6)


    def _build_disk_opts(self):
        f = self.disk_opts = ttk.LabelFrame(self.options_container, text="Disk Options")
        self.disk_size = tk.StringVar(value="1G")
        self.disk_runtime = tk.IntVar(value=60)
        self.disk_filename = tk.StringVar(value=os.path.join(os.getcwd(), "fio_testfile.bin"))
        ttk.Label(f, text="Size:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(f, textvariable=self.disk_size, width=10).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Runtime (s):").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        ttk.Spinbox(f, from_=5, to=3600, textvariable=self.disk_runtime, width=7).grid(row=0, column=3, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Filename:").grid(row=0, column=4, sticky="w", padx=4, pady=4)
        ttk.Entry(f, textvariable=self.disk_filename, width=40).grid(row=0, column=5, sticky="w", padx=4, pady=4)

    def _build_net_opts(self):
        f = self.net_opts = ttk.LabelFrame(self.options_container, text="Network Options")
        self.net_server_ip = tk.StringVar(value="")
        self.net_extra_args = tk.StringVar(value="")
        ttk.Label(f, text="iperf3 Server IP:").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(f, textvariable=self.net_server_ip, width=18).grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(f, text="Extra args (optional):").grid(row=0, column=2, sticky="w", padx=4, pady=4)
        ttk.Entry(f, textvariable=self.net_extra_args, width=30).grid(row=0, column=3, sticky="w", padx=4, pady=4)

    def _show_only_options(self, which: str):
        for f in [self.cpu_opts, self.ram_opts, self.gpu_opts, self.disk_opts, self.net_opts]:
            f.grid_forget()
        {"cpu": self.cpu_opts, "ram": self.ram_opts, "gpu": self.gpu_opts,
         "disk": self.disk_opts, "net": self.net_opts}[which].grid(row=0, column=0, sticky="ew")

    # ---------- Events ----------

    def _on_test_change(self):
        self._show_only_options(self.test_var.get())

    def _start_clicked(self):
        if self.runner.is_running():
            messagebox.showwarning("Busy", "A test is already running.")
            return
        test = self.test_var.get()
        cmd, expected, env = self._build_command(test)
        if not cmd:
            return

        self.current_log_path = os.path.join(LOG_DIR, f"{test}_{timestamp()}.log")
        try:
            self.log_fp = open(self.current_log_path, "w", encoding="utf-8")
            self.log_fp.write(f"{APP_NAME} Log - {datetime.datetime.now().isoformat()}\n")
            self.log_fp.write(f"Command: {' '.join(cmd) if isinstance(cmd, (list, tuple)) else cmd}\n\n")
            if env:
                self.log_fp.write(f"Environment: {env}\n\n")
        except Exception as e:
            self.log_fp = None
            messagebox.showerror("Logging Error", f"Cannot write log file: {e}")
            return

        self._set_running_ui(True, expected)
        self._append_output(f"Starting: {cmd if isinstance(cmd, str) else ' '.join(cmd)}\n")
        self.status_var.set("Running…")

        try:
            self.runner.start(cmd, shell=False, env=env)
        except Exception as e:
            self._append_output(f"\nException starting command: {e}\n")
            self._set_running_ui(False)
            return

        if not self._ui_updater_running:
            self._ui_updater_running = True
            self.after(200, self._tick_progress)

    def _stop_clicked(self):
        if self.runner.is_running():
            self._append_output("\nStopping… attempting graceful termination.\n")
            self.runner.stop()
            self.status_var.set("Stopping…")

    def _on_exit(self):
        if self.runner.is_running():
            if not messagebox.askyesno("Exit", "A test is running. Stop it and exit?"):
                return
            self.runner.stop()
            time.sleep(0.5)
        self.destroy()

    # ---------- Command builders ----------

    def _build_command(self, test: str):
        for cmd in REQUIRED_CMDS[test]:
            if shutil.which(cmd) is None:
                messagebox.showerror("Missing Dependency", f"'{cmd}' not found.\nInstall with:\n{which_or_hint(cmd)}")
                return None, None, None

        if test == "cpu":
            workers = max(1, int(self.cpu_workers.get()))
            duration = max(5, int(self.cpu_timeout.get()))
            return ["stress-ng", "--cpu", str(workers), "--timeout", f"{duration}s"], duration, None

        if test == "ram":
            vm = max(1, int(self.ram_vm_workers.get()))
            vm_bytes = self.ram_bytes.get().strip() or "512M"
            duration = max(5, int(self.ram_timeout.get()))
            return ["stress-ng", "--vm", str(vm), "--vm-bytes", vm_bytes, "--timeout", f"{duration}s"], duration, None

        if test == "gpu":
            return ["glmark2"], None, None

        if test == "disk":
            size = self.disk_size.get().strip() or "1G"
            runtime = max(5, int(self.disk_runtime.get()))
            filename = self.disk_filename.get().strip() or os.path.join(os.getcwd(), "fio_testfile.bin")
            ioengine = "libaio" if platform.system() == "Linux" else "psync"
            return [
                "fio", "--name=randrw", "--rw=randrw", f"--size={size}",
                f"--runtime={runtime}", "--time_based=1", f"--filename={filename}",
                f"--ioengine={ioengine}", "--direct=1",
            ], runtime, None

        if test == "net":
            server = self.net_server_ip.get().strip()
            if not server:
                messagebox.showwarning("Input Error", "Please enter the iperf3 server IP.")
                return None, None, None
            extra = self.net_extra_args.get().strip()
            cmd = ["iperf3", "-c", server]
            if extra:
                cmd.extend(shlex.split(extra))
            return cmd, None, None

        return None, None, None

    # ---------- Output & logging ----------

    def _on_line(self, line: str):
        self.output_queue.put(line)
        try:
            if self.log_fp:
                self.log_fp.write(line)
        except Exception:
            pass

    def _on_done(self, returncode: int | None):
        self.output_queue.put(f"\nProcess finished with return code: {returncode}\n")
        self.output_queue.put("__DONE__")

    def _drain_output_queue(self):
        try:
            while True:
                item = self.output_queue.get_nowait()
                if item == "__DONE__":
                    if self.log_fp:
                        try:
                            self.log_fp.flush(); self.log_fp.close()
                        except Exception:
                            pass
                        self.log_fp = None
                    self._set_running_ui(False)
                    break
                self._append_output(item)
        except queue.Empty:
            pass
        self.after(100, self._drain_output_queue)

    def _append_output(self, text: str):
        self.output_text.insert(tk.END, text)
        self.output_text.see(tk.END)

    def _clear_output(self):
        self.output_text.delete(1.0, tk.END)

    def _save_output_as(self):
        content = self.output_text.get(1.0, tk.END)
        if not content.strip():
            messagebox.showinfo("Save Output", "Nothing to save.")
            return
        fn = filedialog.asksaveasfilename(defaultextension=".txt",
                                          filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if not fn:
            return
        try:
            with open(fn, "w", encoding="utf-8") as f:
                f.write(content)
            messagebox.showinfo("Save Output", f"Saved to:\n{fn}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    def _open_log_folder(self):
        path = LOG_DIR
        try:
            if platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            elif platform.system() == "Windows":
                os.startfile(path)  # type: ignore
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open Folder Error", str(e))

    # ---------- Progress & status ----------

    def _set_running_ui(self, running: bool, expected: int | None = None):
        if running:
            self.start_btn.config(state=tk.DISABLED)
            self.stop_btn.config(state=tk.NORMAL)
            self.test_start_time = time.time()
            self.expected_duration = expected
            if expected and expected > 0:
                self.progress.config(mode="determinate", maximum=expected, value=0)
                self.eta_label.config(text="ETA: calculating…")
            else:
                self.progress.config(mode="indeterminate"); self.progress.start(100)
                self.eta_label.config(text="ETA: --:--")
        else:
            self.start_btn.config(state=tk.NORMAL)
            self.stop_btn.config(state=tk.DISABLED)
            self.status_var.set("Ready.")
            self.test_start_time = None
            if str(self.progress["mode"]) == "indeterminate":
                self.progress.stop()
            self.progress.config(mode="determinate", maximum=100, value=0)
            self.eta_label.config(text="ETA: --:--")

    def _tick_progress(self):
        if self.runner.is_running():
            if self.expected_duration and self.test_start_time:
                elapsed = time.time() - self.test_start_time
                value = min(self.expected_duration, max(0, elapsed))
                self.progress.config(value=value)
                remaining = max(0, int(self.expected_duration - elapsed))
                mm, ss = divmod(remaining, 60)
                self.eta_label.config(text=f"ETA: {mm:02d}:{ss:02d}")
        else:
            self._ui_updater_running = False
            return
        self.after(200, self._tick_progress)

    # ---------- Dashboard updater ----------

    def _update_dashboard(self):
        if not HAS_PSUTIL:
            return
        try:
            cpu = psutil.cpu_percent(interval=None)
            self.gauge_cpu.set(cpu / 100.0, "")

            vm = psutil.virtual_memory()
            self.gauge_mem.set(vm.percent / 100.0, f"{vm.used/(1024**3):.1f} GiB / {vm.total/(1024**3):.1f} GiB")

            du = psutil.disk_usage("/")
            self.gauge_dsk.set(du.percent / 100.0, f"{du.used/(1024**3):.1f} GiB / {du.total/(1024**3):.1f} GiB")
        except Exception as e:
            self.status_var.set(f"Dashboard error: {e}")

        self.after(1000, self._update_dashboard)

    # ---------- Theme handling ----------

    def _apply_theme(self):
        style = ttk.Style()
        mode = self.theme.get()
        if mode == "dark":
            bg = "#1f2937"; track = "#0b1620"
            text = "#000000"; caption = "#000000"
            try: style.theme_use("clam")
            except Exception: pass
            style.configure(".", background=bg, foreground="#E5E7EB")
            self.configure(bg=bg)
            self.output_text.configure(bg="#0f172a", fg="#E5E7EB", insertbackground="#E5E7EB")
        else:
            bg = "#EFEFEF"; track = "#c7ced6"
            text = "#000000"; caption = "#000000"
            try: style.theme_use("clam")
            except Exception: pass
            style.configure(".", background=bg, foreground="#111827")
            self.configure(bg=bg)
            self.output_text.configure(bg="#FFFFFF", fg="#111827", insertbackground="#111827")

        for g in (self.gauge_cpu, self.gauge_mem, self.gauge_dsk):
            g.set_theme(bg=bg, track=track, text=text, caption=caption,
                        color_code_caption=self.caption_colored.get())

    def _apply_caption_color_mode(self):
        for g in (self.gauge_cpu, self.gauge_mem, self.gauge_dsk):
            g.caption_color_coded = self.caption_colored.get()
            g._refresh_caption_color()

    # ---------- Dependency checks ----------

    def _check_dependencies_summary(self):
        missing = []
        for cmds in REQUIRED_CMDS.values():
            for c in cmds:
                if shutil.which(c) is None and c not in missing:
                    missing.append(c)
        if missing:
            self.status_var.set(f"Missing tools: {', '.join(missing)} (see Tools → Check Dependencies)")

    def _check_dependencies_dialog(self):
        lines = [f"Dependency check ({platform.system()})"]
        for cmd in sorted({c for v in REQUIRED_CMDS.values() for c in v}):
            lines.append(f" - {cmd}: {which_or_hint(cmd)}")
        if not HAS_PSUTIL:
            lines.append(" - psutil: NOT FOUND (pip install psutil)")
        else:
            lines.append(" - psutil: OK")
        messagebox.showinfo("Dependencies", "\n".join(lines))

    # ---------- About ----------

    def _show_about(self):
        messagebox.showinfo(
            "About",
            f"{APP_NAME}\n"
            f"Version {VERSION}\n"
            f"Revision Date: {REVISION_DATE}\n"
            f"Author: {AUTHOR}\n\n"
            "CPU/RAM via stress-ng\n"
            "GPU via glmark2\n"
            "Disk via fio\n"
            "Network via iperf3\n"
            "Dashboard via psutil\n\n"
            "Logs are saved to:\n"
            f"{LOG_DIR}"
        )

# -------------------------
# Entry point
# -------------------------

def main():
    app = StressTestApp()
    try:
        style = ttk.Style()
        if platform.system() != "Windows":
            style.theme_use("clam")
    except Exception:
        pass
    app.mainloop()

if __name__ == "__main__":
    main()

