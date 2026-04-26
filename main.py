import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import threading
import subprocess
import csv
import os
import re
import shutil
from PIL import Image, ImageTk

# ── CONFIG ────────────────────────────────────────────────────────────────────
MULTITHREAD_PATH    = "multithread.py"
VISUALIZER_PATH     = "visualizer.py"
VISUALIZER_BTN_PATH = "visualizer_boto.py"
BEST_OUTPUT_CSV     = "best_output.csv"
OUTPUT_CSV          = "output.csv"
OUTPUT_IMG          = "layout_final.png"

BEST_Q_PATTERN = re.compile(r"[Mm]ejor(?:\s*Q\s*encontrado)?\s*[:\(]\s*([\d.]+)")

# ── COLORS / FONTS ────────────────────────────────────────────────────────────
BG        = "#0d0f14"
PANEL     = "#13161e"
ACCENT    = "#00e5a0"
ACCENT2   = "#0077ff"
ACCENT3   = "#f59e0b"
YELLOW    = "#f0c040"
TEXT      = "#e8eaf0"
SUBTEXT   = "#6b7280"
BORDER    = "#1f2330"
PROG_BG   = "#1a1e2a"
PROG_FG   = "#00e5a0"
PROG_GLOW = "#00c87a"
FONT_MONO = ("Courier New", 12)
FONT_HEAD = ("Courier New", 13, "bold")
FONT_TITLE= ("Courier New", 18, "bold")
FONT_Q    = ("Courier New", 24, "bold")
FONT_QLBL = ("Courier New", 11)

# Progress bar duration in ms
PROGRESS_DURATION_MS = 30_000
PROGRESS_STEPS       = 300   # one tick every 100 ms


class ProgressBar(tk.Canvas):
    """Custom animated progress bar with glow effect."""

    def __init__(self, parent, **kwargs):
        height = kwargs.pop("bar_height", 6)
        super().__init__(parent, height=height, bg=PROG_BG,
                         highlightthickness=0, **kwargs)
        self._pct      = 0.0   # 0.0 – 1.0
        self._job      = None
        self._running  = False
        self._step     = 0
        self.bind("<Configure>", lambda e: self._redraw())

    # ── public API ────────────────────────────────────────────────────────────
    def start(self):
        """Begin a 30-second fill from 0 → 100 %."""
        self._pct     = 0.0
        self._step    = 0
        self._running = True
        self._tick()

    def finish(self):
        """Instantly fill to 100 % and stop."""
        self._running = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._pct = 1.0
        self._redraw()
        # Fade out after a moment
        self.after(800, self._fade_out)

    def reset(self):
        """Return to 0 % immediately."""
        self._running = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        self._pct = 0.0
        self._redraw()

    # ── internals ────────────────────────────────────────────────────────────
    def _tick(self):
        if not self._running:
            return
        self._step += 1
        # Ease-in-out curve so the bar slows near the end
        t = min(self._step / PROGRESS_STEPS, 1.0)
        self._pct = t * t * (3 - 2 * t)   # smoothstep
        self._redraw()
        if self._step < PROGRESS_STEPS:
            interval = PROGRESS_DURATION_MS // PROGRESS_STEPS
            self._job = self.after(interval, self._tick)
        else:
            self._running = False

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 2:
            return
        # Background track
        self.create_rectangle(0, 0, w, h, fill=PROG_BG, outline="")
        # Filled portion
        fill_w = int(w * self._pct)
        if fill_w > 0:
            # Main bar
            self.create_rectangle(0, 0, fill_w, h, fill=PROG_FG, outline="")
            # Glow highlight (top half, lighter)
            self.create_rectangle(0, 0, fill_w, h // 2,
                                  fill=PROG_GLOW, outline="", stipple="gray50")
            # Leading edge shimmer
            if fill_w > 6:
                self.create_rectangle(fill_w - 4, 0, fill_w, h,
                                      fill="#80ffda", outline="")

    def _fade_out(self):
        """Gradually reduce opacity by stepping down brightness."""
        if self._pct <= 0:
            return
        self._pct = max(0.0, self._pct - 0.05)
        self._redraw()
        if self._pct > 0:
            self.after(30, self._fade_out)


class WarehouseGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Warehouse Optimizer")
        self.configure(bg=BG)
        self.geometry("1440x920")
        self.minsize(960, 640)
        self._img_ref  = None
        self._raw_img  = None
        self._best_q   = None
        self._build_ui()

    # ── UI CONSTRUCTION ───────────────────────────────────────────────────────
    def _build_ui(self):
        # top bar
        top = tk.Frame(self, bg=BG, pady=14, padx=20)
        top.pack(fill="x")
        tk.Label(top, text="WAREHOUSE OPTIMIZER", font=FONT_TITLE,
                 fg=ACCENT, bg=BG).pack(side="left")
        btn_frame = tk.Frame(top, bg=BG)
        btn_frame.pack(side="right")

        self.run_btn = self._btn(btn_frame, "▶  RUN", self._start_run, ACCENT)
        self.run_btn.pack(side="left", padx=(0, 10))

        self.vis_btn = self._btn(btn_frame, "◈  VISUALIZER", self._open_visualizer, ACCENT2)
        self.vis_btn.pack(side="left", padx=(0, 10))
        self.vis_btn.config(state="disabled")

        self.dl_btn = self._btn(btn_frame, "⬇  DOWNLOAD RESULT", self._download_result, ACCENT3)
        self.dl_btn.pack(side="left")
        self.dl_btn.config(state="disabled")

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── PROGRESS BAR (full width, just below top bar) ─────────────────────
        prog_container = tk.Frame(self, bg=BG, padx=20, pady=6)
        prog_container.pack(fill="x")

        prog_row = tk.Frame(prog_container, bg=BG)
        prog_row.pack(fill="x")

        self.prog_label = tk.Label(prog_row, text="", font=("Courier New", 9),
                                   fg=SUBTEXT, bg=BG, width=22, anchor="w")
        self.prog_label.pack(side="left")

        self.prog_pct_lbl = tk.Label(prog_row, text="", font=("Courier New", 9, "bold"),
                                     fg=ACCENT, bg=BG, width=6, anchor="e")
        self.prog_pct_lbl.pack(side="right")

        self.progress = ProgressBar(prog_container, bar_height=7)
        self.progress.pack(fill="x", pady=(2, 0))

        # Separator
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # status bar
        self.status_var = tk.StringVar(value="Ready.")
        status_bar = tk.Frame(self, bg=PANEL, pady=6, padx=20)
        status_bar.pack(fill="x")
        tk.Label(status_bar, textvariable=self.status_var, font=FONT_MONO,
                 fg=SUBTEXT, bg=PANEL, anchor="w").pack(side="left", fill="x", expand=True)
        self.spinner_lbl = tk.Label(status_bar, text="", font=FONT_MONO,
                                    fg=ACCENT, bg=PANEL, width=3)
        self.spinner_lbl.pack(side="right")

        # main panes
        paned = tk.PanedWindow(self, orient="horizontal", bg=BG,
                               sashwidth=4, sashpad=0, sashrelief="flat")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        # ── LEFT ──────────────────────────────────────────────────────────────
        left = tk.Frame(paned, bg=BG)
        paned.add(left, minsize=380)

        # Best Q badge
        q_frame = tk.Frame(left, bg=PANEL,
                           highlightthickness=1, highlightbackground=BORDER)
        q_frame.pack(fill="x", pady=(0, 10))
        tk.Label(q_frame, text="BEST Q FOUND", font=FONT_QLBL,
                 fg=SUBTEXT, bg=PANEL, pady=10, padx=14).pack(side="left")
        self.q_var = tk.StringVar(value="—")
        tk.Label(q_frame, textvariable=self.q_var, font=FONT_Q,
                 fg=YELLOW, bg=PANEL, padx=14).pack(side="right")

        # Log
        tk.Label(left, text="PROCESS LOG", font=FONT_HEAD,
                 fg=SUBTEXT, bg=BG, anchor="w").pack(fill="x", pady=(0, 4))
        log_frame = tk.Frame(left, bg=PANEL,
                             highlightthickness=1, highlightbackground=BORDER)
        log_frame.pack(fill="x")
        self.log_text = tk.Text(log_frame, height=10, bg=PANEL, fg=TEXT,
                                font=FONT_MONO, insertbackground=ACCENT,
                                relief="flat", padx=10, pady=8,
                                wrap="word", state="disabled")
        log_scroll = tk.Scrollbar(log_frame, command=self.log_text.yview, bg=PANEL)
        self.log_text.config(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="x")

        # Table
        tk.Label(left, text="BEST OUTPUT  (best_output.csv)", font=FONT_HEAD,
                 fg=SUBTEXT, bg=BG, anchor="w").pack(fill="x", pady=(12, 4))
        tbl_outer = tk.Frame(left, bg=PANEL,
                             highlightthickness=1, highlightbackground=BORDER)
        tbl_outer.pack(fill="both", expand=True)

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Custom.Treeview",
                        background=PANEL, foreground=TEXT,
                        rowheight=28, fieldbackground=PANEL,
                        bordercolor=BORDER, borderwidth=0,
                        font=FONT_MONO)
        style.configure("Custom.Treeview.Heading",
                        background=BG, foreground=ACCENT,
                        font=FONT_HEAD, borderwidth=0, relief="flat")
        style.map("Custom.Treeview",
                  background=[("selected", ACCENT2)],
                  foreground=[("selected", "#ffffff")])
        self.tree = ttk.Treeview(tbl_outer, style="Custom.Treeview",
                                 show="headings", selectmode="browse")
        vsb = ttk.Scrollbar(tbl_outer, orient="vertical",   command=self.tree.yview)
        hsb = ttk.Scrollbar(tbl_outer, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        hsb.pack(side="bottom", fill="x")
        vsb.pack(side="right",  fill="y")
        self.tree.pack(fill="both", expand=True)

        # ── RIGHT ─────────────────────────────────────────────────────────────
        right = tk.Frame(paned, bg=BG)
        paned.add(right, minsize=640)   # wider minimum for the preview

        tk.Label(right, text="LAYOUT PREVIEW  (layout_final.png)", font=FONT_HEAD,
                 fg=SUBTEXT, bg=BG, anchor="w").pack(fill="x", pady=(0, 4))

        self.canvas = tk.Canvas(right, bg=PANEL, highlightthickness=1,
                                highlightbackground=BORDER)
        self.canvas.pack(fill="both", expand=True)
        self._canvas_img_id = None
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        # Kick off a repeating percentage update for the progress label
        self._update_prog_label()

    # ── PROGRESS LABEL TICKER ─────────────────────────────────────────────────
    def _update_prog_label(self):
        pct = int(self.progress._pct * 100)
        if self.progress._running or self.progress._pct > 0:
            self.prog_pct_lbl.config(text=f"{pct} %")
        else:
            self.prog_pct_lbl.config(text="")
        self.after(100, self._update_prog_label)

    # ── HELPERS ───────────────────────────────────────────────────────────────
    @staticmethod
    def _btn(parent, text, cmd, color):
        return tk.Button(parent, text=text, command=cmd,
                         bg=color, fg="#000000", activebackground=color,
                         font=("Courier New", 11, "bold"),
                         relief="flat", padx=16, pady=7, cursor="hand2", bd=0)

    def _log(self, msg):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.config(state="disabled")
        m = BEST_Q_PATTERN.search(msg)
        if m:
            val = m.group(1)
            try:
                if self._best_q is None or float(val) < float(self._best_q):
                    self._best_q = val
                    self.q_var.set(val)
            except ValueError:
                pass

    def _set_status(self, msg):
        self.status_var.set(msg)

    # ── SPINNER ───────────────────────────────────────────────────────────────
    _SPIN = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    _spin_idx = 0
    _spinning = False

    def _start_spinner(self):
        self._spinning = True
        self._tick_spinner()

    def _tick_spinner(self):
        if not self._spinning:
            self.spinner_lbl.config(text="")
            return
        self.spinner_lbl.config(text=self._SPIN[self._spin_idx % len(self._SPIN)])
        self._spin_idx += 1
        self.after(80, self._tick_spinner)

    def _stop_spinner(self):
        self._spinning = False

    # ── RUN ───────────────────────────────────────────────────────────────────
    def _start_run(self):
        self.run_btn.config(state="disabled")
        self.vis_btn.config(state="disabled")
        self.dl_btn.config(state="disabled")
        self._best_q = None
        self.q_var.set("—")
        self._set_status("Running optimizer…")
        self._start_spinner()
        # Start the visual progress bar
        self.progress.reset()
        self.prog_label.config(text="Optimizing…", fg=ACCENT)
        self.progress.start()
        threading.Thread(target=self._run_optimizer, daemon=True).start()

    def _run_optimizer(self):
        self.after(0, lambda: self._log(f"[RUN] {MULTITHREAD_PATH}"))
        try:
            proc = subprocess.Popen(
                ["python", MULTITHREAD_PATH],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.after(0, lambda l=line: self._log(f"  {l}"))
            proc.wait()
            rc = proc.returncode
            self.after(0, lambda: self._on_run_done(rc))
        except FileNotFoundError:
            self.after(0, lambda: self._on_run_error(f"'{MULTITHREAD_PATH}' not found."))
        except Exception as e:
            msg = str(e)
            self.after(0, lambda m=msg: self._on_run_error(m))

    def _on_run_done(self, returncode):
        self._stop_spinner()
        if returncode != 0:
            self.progress.reset()
            self.prog_label.config(text="")
            self._on_run_error(f"Process exited with code {returncode}.")
            return
        # Snap bar to 100 % on success
        self.progress.finish()
        self.prog_label.config(text="Complete ✓", fg=ACCENT)
        self._log(f"[DONE] exit code {returncode}")
        self._set_status("Running visualizer…")
        self._start_spinner()
        threading.Thread(target=self._run_visualizer_then_load, daemon=True).start()

    def _run_visualizer_then_load(self):
        self.after(0, lambda: self._log(f"[VIS] Running {VISUALIZER_PATH}…"))
        try:
            proc = subprocess.Popen(
                ["python", VISUALIZER_PATH],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.after(0, lambda l=line: self._log(f"  {l}"))
            proc.wait()
        except Exception as e:
            self.after(0, lambda m=str(e): self._log(f"[VIS ERROR] {m}"))
        self.after(0, self._finish_loading)

    def _finish_loading(self):
        self._stop_spinner()
        self._load_csv()
        self._raw_img = None
        self.canvas.delete("all")
        self._load_image()
        self.update_idletasks()
        self._draw_image()
        self._set_status("Results loaded." + (f"  Best Q = {self._best_q}" if self._best_q else ""))
        self.run_btn.config(state="normal")
        self.vis_btn.config(state="normal")
        if os.path.exists(OUTPUT_CSV):
            self.dl_btn.config(state="normal")

    def _on_run_error(self, msg):
        self._stop_spinner()
        self.progress.reset()
        self.prog_label.config(text="Failed ✗", fg="#ff4444")
        self._log(f"[ERROR] {msg}")
        self._set_status(f"Error: {msg}")
        messagebox.showerror("Error", msg)
        self.run_btn.config(state="normal")

    # ── DOWNLOAD output.csv ───────────────────────────────────────────────────
    def _download_result(self):
        src = os.path.abspath(OUTPUT_CSV)
        if not os.path.exists(src):
            messagebox.showwarning("Not Available",
                                   f"'{OUTPUT_CSV}' not found.\n"
                                   "Run the optimizer first.")
            return
        dest = filedialog.asksaveasfilename(
            title="Save final result",
            initialfile="output.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not dest:
            return
        try:
            shutil.copy2(src, dest)
            self._log(f"[DOWNLOAD] output.csv saved to: {dest}")
            self._set_status(f"Result saved: {os.path.basename(dest)}")
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    # ── CSV TABLE ─────────────────────────────────────────────────────────────
    def _load_csv(self):
        if not os.path.exists(BEST_OUTPUT_CSV):
            self._log(f"[WARN] {BEST_OUTPUT_CSV} not found.")
            return
        with open(BEST_OUTPUT_CSV, newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return
        headers, data = rows[0], rows[1:]
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = headers
        col_w = max(90, 860 // max(len(headers), 1))
        for h in headers:
            self.tree.heading(h, text=h)
            self.tree.column(h, width=col_w, anchor="center", minwidth=70)
        for i, row in enumerate(data):
            self.tree.insert("", "end", values=row,
                             tags=("odd" if i % 2 else "even",))
        self.tree.tag_configure("odd",  background="#161922")
        self.tree.tag_configure("even", background=PANEL)
        self._log(f"[CSV] {len(data)} rows loaded.")

    # ── IMAGE (Canvas) ────────────────────────────────────────────────────────
    def _load_image(self):
        if not os.path.exists(OUTPUT_IMG):
            self._log(f"[WARN] {OUTPUT_IMG} not found.")
            return
        with open(OUTPUT_IMG, "rb") as f:
            raw_bytes = f.read()
        import io
        img = Image.open(io.BytesIO(raw_bytes))
        img.load()
        self._raw_img = img.copy()
        mtime = os.path.getmtime(OUTPUT_IMG)
        self._log(f"[IMG] {OUTPUT_IMG} reloaded "
                  f"({self._raw_img.width}x{self._raw_img.height}px, mtime={mtime:.0f}).")

    def _draw_image(self):
        if self._raw_img is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 2 or ch < 2:
            return
        iw, ih = self._raw_img.size
        # Use 96 % of the canvas so there's just a thin breathing margin
        scale = min(cw * 1.2 / iw, ch * 1.2 / ih)
        nw = max(1, int(iw * scale))
        nh = max(1, int(ih * scale))
        resized = self._raw_img.resize((nw, nh), Image.LANCZOS)
        self._img_ref = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(cw // 2, ch // 2,
                                 anchor="center", image=self._img_ref)
        self._log(f"[DRAW] canvas={cw}x{ch} img={nw}x{nh}")

    def _on_canvas_resize(self, event):
        if self._raw_img is not None:
            self.after(150, self._draw_image)

    # ── VISUALIZER (manual button) ────────────────────────────────────────────
    def _open_visualizer(self):
        if not os.path.exists(VISUALIZER_BTN_PATH):
            messagebox.showerror("Error", f"'{VISUALIZER_BTN_PATH}' not found.")
            return
        self._log(f"[VIS] Launching {VISUALIZER_BTN_PATH} (manual)…")
        try:
            subprocess.Popen(["python", VISUALIZER_BTN_PATH])
        except Exception as e:
            messagebox.showerror("Error", str(e))


if __name__ == "__main__":
    app = WarehouseGUI()
    app.mainloop()