# Then use ADB_PATH in your subprocess calls

import tkinter as tk
from tkinter import messagebox
import subprocess, time, io, json, os, sys, math, shutil
import numpy as np
from PIL import Image, ImageTk, ImageDraw, ImageFont

# Suppress terminal flicker on Windows when spawning ADB via subprocess
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Brand theme (matches gui_app.py) ────────────────────────────
ACCENT       = "#df7d59"   # primary brand color
ACCENT_HOVER = "#c5663f"   # darker, for hover states
ACCENT_DARK  = "#7a4530"   # dark filled variant (toolbar buttons)

# ── Config ────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
# From source the script sits in src/, so bin/ and assets/ are one level up;
# when frozen-bundled it runs from the _MEIPASS root where adb ships flat.
PROJECT_ROOT = os.path.dirname(BASE_DIR)

def _resolve_adb():
    if sys.platform != "win32":
        return "adb"
    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "KrakenPrime", "adb")
    dest_adb = os.path.join(dest_dir, "adb.exe")
    if os.path.isfile(dest_adb):
        return dest_adb
    src_candidates = [getattr(sys, "_MEIPASS", None), BASE_DIR, os.path.join(PROJECT_ROOT, "bin")]
    src_dir = next((d for d in src_candidates
                    if d and os.path.isfile(os.path.join(d, "adb.exe"))), None)
    if src_dir:
        try:
            os.makedirs(dest_dir, exist_ok=True)
            for fname in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
                src = os.path.join(src_dir, fname)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(dest_dir, fname))
            if os.path.isfile(dest_adb):
                return dest_adb
        except Exception as e:
            print(f"[overlay] Could not extract bundled adb.exe: {e}")
    for cand in (os.path.join(BASE_DIR, "adb.exe"), os.path.join(PROJECT_ROOT, "bin", "adb.exe")):
        if os.path.isfile(cand):
            return cand
    return "adb"

ADB = _resolve_adb()
DEVICE       = "127.0.0.1:5555"
# These describe the LDPlayer / device resolution — the coordinate
# space that ADB taps are ultimately sent in. They stay fixed
# regardless of the size of the window on screen.
DEVICE_W     = 1600
DEVICE_H     = 900
SCREENSHOT_W = 1600
SCREENSHOT_H = 900

# How much of the available screen we're willing to use (leaving room
# for the toolbar, status bar, and window chrome/taskbar), and a
# sensible floor so the canvas never gets too small to use.
MIN_DISPLAY_W = 480
MAX_SCREEN_FRACTION_W = 0.90
MAX_SCREEN_FRACTION_H = 0.80
CHROME_RESERVE_H = 110   # approx height used by toolbar + status labels

# Read env vars from app.py (or use defaults for standalone)
POINTS_FILE   = os.environ.get("OVERLAY_OUTPUT", "deploy_points.json")
TITLE_HINT    = os.environ.get("OVERLAY_TITLE",  "COC Deployment Planner")
SENTINEL_FILE = os.environ.get("OVERLAY_SENTINEL", None)  # Signals cancel if still exists on close

# Rectangle-clip mode: when OVERLAY_CLIP_OUTPUT is set, the overlay lets the
# user drag a box instead of placing dots, and on save writes the cropped
# region of the screenshot to that PNG path (used to capture "resource full"
# reference images for template matching).
CLIP_OUTPUT   = os.environ.get("OVERLAY_CLIP_OUTPUT", None)
CLIP_MODE     = CLIP_OUTPUT is not None

# Rectangle-coordinate mode: like clip mode (drag a box), but on save it writes
# the box's device-space coordinates {"roi":[x1,y1,x2,y2]} to a JSON path,
# instead of a cropped image. Used to define OCR regions (e.g. scout loot boxes).
RECT_OUTPUT   = os.environ.get("OVERLAY_RECT_OUTPUT", None)
RECT_MODE     = RECT_OUTPUT is not None

# Both clip and rect modes use the drag-a-rectangle UI.
DRAG_MODE     = CLIP_MODE or RECT_MODE

# Multi-layer mode: OVERLAY_LAYERS is a JSON list of point layers to edit in one
# window, e.g. [{"key","label","file","color"}, ...]. Checkboxes at the top-right
# toggle each layer's visibility and pick which layer new clicks pin to. On save,
# each layer with points is written to its file.
LAYERS_JSON   = os.environ.get("OVERLAY_LAYERS", None)
MULTI_MODE    = LAYERS_JSON is not None

# Combine mode (sub-mode of multi): all layers are two views of ONE file. On load
# the file's list is split across layers by their counts; on save the layers are
# concatenated back in order and written to this file (used for the troop bar
# slots, which the engine reads as one ordered "slots" list).
COMBINE_FILE  = os.environ.get("OVERLAY_COMBINE_FILE", None)

# When OVERLAY_CENTER=1, do the same 45° diagonal swipe the bot performs after
# finding a base (center_screen), ONCE before the first screenshot — so deploy
# / rage points are pinned on the exact camera view the bot deploys from.
CENTER_FIRST   = os.environ.get("OVERLAY_CENTER") == "1"
SWIPE_ANGLE    = 45
SWIPE_DISTANCE = 300
SWIPE_DURATION = 300

def center_screen():
    cx, cy = DEVICE_W // 2, DEVICE_H // 2
    rad = math.radians(SWIPE_ANGLE)
    dx  = int(math.cos(rad) * SWIPE_DISTANCE)
    dy  = int(math.sin(rad) * SWIPE_DISTANCE)
    subprocess.run(
        [ADB, "-s", DEVICE, "shell", "input", "touchscreen",
         "swipe", str(cx), str(cy), str(cx + dx), str(cy - dy), str(SWIPE_DURATION)],
        capture_output=True, creationflags=_NO_WINDOW
    )
    time.sleep(0.6)

# ── Coordinate helpers ──────────────────────────────────────────
# These take the *current* on-screen display size explicitly, since
# the window/canvas can now be resized (or start smaller to fit
# smaller screens/laptops) instead of always being a fixed 1600x900
# canvas.

def display_to_device(dx, dy, display_w, display_h):
    devx = int(dx / display_w * DEVICE_W)
    devy = int(dy / display_h * DEVICE_H)
    return devx, devy

def device_to_display(devx, devy, display_w, display_h):
    dx = int(devx / DEVICE_W * display_w)
    dy = int(devy / DEVICE_H * display_h)
    return dx, dy

# ── ADB screenshot ────────────────────────────────────────────

def grab_screenshot():
    try:
        result = subprocess.run(
            [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
            capture_output=True, timeout=12, creationflags=_NO_WINDOW
        )
        if not result.stdout or len(result.stdout) < 1000:
            return None
        img = Image.open(io.BytesIO(result.stdout))
        img = img.resize((SCREENSHOT_W, SCREENSHOT_H), Image.LANCZOS)
        return img
    except Exception as e:
        print(f"[overlay] Screenshot error: {e}")
        return None

# ── Dot drawing ───────────────────────────────────────────────

DOT_RADIUS = 10
DOT_COLORS = ["#FF4444", "#FF8C00", "#FFD700", "#44FF88",
              "#00BFFF", "#BF7FFF", "#FF69B4", "#FFFFFF"]

def draw_dots_on_image(pil_img, points_display):
    img  = pil_img.copy()
    draw = ImageDraw.Draw(img)
    for i, (dx, dy) in enumerate(points_display):
        color = DOT_COLORS[i % len(DOT_COLORS)]
        r = DOT_RADIUS
        draw.ellipse([dx-r-2, dy-r-2, dx+r+2, dy+r+2], fill="white", outline="white")
        draw.ellipse([dx-r,   dy-r,   dx+r,   dy+r  ], fill=color,   outline="black")
        label = str(i + 1)
        try:
            font = ImageFont.truetype("arial.ttf", 11)
        except Exception:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        draw.text((dx - tw//2, dy - th//2), label, fill="black", font=font)
    return img

# ── Main overlay app ──────────────────────────────────────────

class DeployOverlay:
    def __init__(self, root):
        self.root = root
        self.root.title(f"⚔  {TITLE_HINT}")
        # Allow the window to be resized so it can be fit to (or
        # adjusted on) any screen — no longer locked to a fixed
        # 1600x900 size.
        self.root.resizable(True, True)
        self.root.configure(bg="#1a1a2e")
        self._set_window_icon()

        self.base_image  = None
        self.tk_image    = None
        self.points_dev  = []   # (devx, devy) device space — source of truth
        # Clip mode (drag a rectangle → save cropped PNG)
        self.clip_rect_dev = None   # (x1, y1, x2, y2) in device space
        self._drag_start   = None   # display-space start of an in-progress drag

        # Multi-layer mode: one dict per layer with its points + visibility.
        self.layers = []
        self.active_idx = 0
        if MULTI_MODE:
            for spec in json.loads(LAYERS_JSON):
                self.layers.append({
                    "key":   spec.get("key", spec.get("label", "layer")),
                    "label": spec.get("label", "Layer"),
                    "file":  spec["file"],
                    "color": spec.get("color", "#39FF14"),
                    "count": int(spec.get("count", 0) or 0),   # target pin count; 0 = unlimited
                    "points": [] if COMBINE_FILE else self._load_layer_points(spec["file"]),
                })
            if COMBINE_FILE:
                # All layers come from one file — split its list by each count.
                raw = self._load_layer_points(COMBINE_FILE)
                pos = 0
                for i, layer in enumerate(self.layers):
                    c = layer["count"]
                    if c and i < len(self.layers) - 1:
                        layer["points"] = raw[pos:pos + c]
                        pos += c
                    else:                       # last layer / no count → remainder
                        layer["points"] = raw[pos:]
                        pos = len(raw)

        # Compute an initial canvas size that fits comfortably on
        # this screen, preserving the device's aspect ratio.
        self.display_w, self.display_h = self._compute_fit_size()
        self._resize_job = None

        self._build_ui()
        if not MULTI_MODE:
            self._try_load_existing_points()
        if CENTER_FIRST:
            # Match the bot's post-scout camera before grabbing the screenshot.
            self.status_var.set("Centering view (45° swipe)…")
            self.root.update()
            center_screen()
        self.refresh_screenshot()

    def _compute_fit_size(self):
        """Pick an on-screen canvas size that fits the current screen
        while preserving the device's aspect ratio."""
        try:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
        except Exception:
            screen_w, screen_h = DEVICE_W, DEVICE_H

        max_w = max(MIN_DISPLAY_W, int(screen_w * MAX_SCREEN_FRACTION_W))
        max_h = max(1, int(screen_h * MAX_SCREEN_FRACTION_H) - CHROME_RESERVE_H)

        aspect = DEVICE_W / DEVICE_H
        # Fit within (max_w, max_h) without ever upscaling past native res.
        scale = min(max_w / DEVICE_W, max_h / DEVICE_H, 1.0)
        w = max(MIN_DISPLAY_W, int(DEVICE_W * scale))
        h = int(w / aspect)
        return w, h

    def _set_window_icon(self):
        """Match gui_app.py branding by using the same icon.ico, if present."""
        # Frozen: bundled at the _MEIPASS root. Dev: in assets/ off the project root.
        for icon_path in (os.path.join(BASE_DIR, "icon.ico"),
                          os.path.join(PROJECT_ROOT, "assets", "icon.ico")):
            if os.path.exists(icon_path):
                try:
                    self.root.iconbitmap(icon_path)
                except Exception:
                    pass
                return

    def _load_layer_points(self, path):
        """Load existing device-space points for one layer. [] if missing."""
        pts = []
        try:
            if os.path.exists(path):
                with open(path) as f:
                    data = json.load(f)
                for p in data.get("points", data.get("slots", [])):
                    pts.append((p["x"], p["y"]))
        except Exception as e:
            print(f"[overlay] Could not load layer {path}: {e}")
        return pts

    # ── UI ────────────────────────────────────────────────────

    def _build_ui(self):
        bar = tk.Frame(self.root, bg="#16213e", pady=6, padx=8)
        bar.pack(fill="x")

        tk.Label(bar, text=f"⚔  {TITLE_HINT}",
                 font=("Consolas", 12, "bold"),
                 fg=ACCENT, bg="#16213e").pack(side="left")

        btn = {"bg": ACCENT_DARK, "fg": "#e0e0e0",
               "activebackground": ACCENT, "activeforeground": "white",
               "relief": "flat", "padx": 12, "pady": 4,
               "font": ("Consolas", 10, "bold"), "cursor": "hand2"}

        tk.Button(bar, text="⟳  Refresh",
                  command=self.refresh_screenshot, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="✕  Clear All",
                  command=self.clear_points, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="↩  Undo",
                  command=self.undo_point, **btn).pack(side="right", padx=4)
        tk.Button(bar, text="💾  Save & Close",
                  command=self.save_and_close,
                  bg=ACCENT, fg="white", activebackground=ACCENT_HOVER,
                  relief="flat", padx=12, pady=4,
                  font=("Consolas", 10, "bold"), cursor="hand2").pack(side="right", padx=4)

        self.canvas = tk.Canvas(self.root,
                                width=self.display_w, height=self.display_h,
                                bg="#0d0d1a", cursor="crosshair",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        if MULTI_MODE:
            self.canvas.bind("<Button-1>", self.on_layer_click)
            self.canvas.bind("<Button-3>", self.on_right_click)
        elif DRAG_MODE:
            # Drag a rectangle: press → move → release.
            self.canvas.bind("<ButtonPress-1>",   self.on_clip_press)
            self.canvas.bind("<B1-Motion>",       self.on_clip_drag)
            self.canvas.bind("<ButtonRelease-1>", self.on_clip_release)
        else:
            self.canvas.bind("<Button-1>", self.on_left_click)
            self.canvas.bind("<Button-3>", self.on_right_click)
        # Keep the screenshot fitted to the window as the user
        # resizes it (e.g. maximizing, or dragging to fit their screen).
        self.canvas.bind("<Configure>", self._on_canvas_resize)

        if MULTI_MODE:
            self._build_layer_panel()

        self.status_var = tk.StringVar(value="Connecting to LDPlayer…")
        tk.Label(self.root, textvariable=self.status_var,
                 font=("Consolas", 9), fg="#888",
                 bg="#1a1a2e", anchor="w", padx=8, pady=4).pack(fill="x")

        if MULTI_MODE:
            hint = ("Pick a layer (top-right) to pin it — left-click places a dot, "
                    "right-click / Undo removes the last. 'View all' shows every layer. Save writes all.")
        elif RECT_MODE:
            hint = ("Drag a box around the number to OCR    "
                    "Clear → reset box    Save & Close → write "
                    + os.path.basename(RECT_OUTPUT))
        elif CLIP_MODE:
            hint = ("Drag a box around the resource number (while it is FULL)    "
                    "Clear → reset box    Save & Close → write "
                    + os.path.basename(CLIP_OUTPUT))
        else:
            hint = ("Left-click → place dot    Right-click / Undo → remove last    "
                    "Save & Close → write " + os.path.basename(POINTS_FILE))
        tk.Label(self.root, text=hint,
                 font=("Consolas", 8), fg="#555", bg="#1a1a2e").pack(pady=(0, 6))

    # ── Screenshot ────────────────────────────────────────────

    def refresh_screenshot(self):
        self.status_var.set("Grabbing screenshot from LDPlayer…")
        self.root.update()
        img = grab_screenshot()
        if img is None:
            self.status_var.set("⚠  Could not connect — is LDPlayer running?")
            img = Image.new("RGB", (SCREENSHOT_W, SCREENSHOT_H), (20, 20, 40))
        self.base_image = img
        self._redraw()
        self.status_var.set(
            f"Screenshot loaded  |  {len(self.points_dev)} point(s)  "
            f"|  saving to: {POINTS_FILE}"
        )

    def _on_canvas_resize(self, event):
        # Debounce: only redraw a short moment after resizing stops,
        # so dragging the window edge doesn't trigger a redraw storm.
        new_w, new_h = event.width, event.height
        if new_w < 10 or new_h < 10:
            return
        self.display_w, self.display_h = new_w, new_h
        if self._resize_job is not None:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(80, self._redraw)

    def _redraw(self):
        self._resize_job = None
        if self.base_image is None:
            return
        display_img = self.base_image.resize((self.display_w, self.display_h), Image.LANCZOS)
        if MULTI_MODE:
            draw = ImageDraw.Draw(display_img)
            try:
                font = ImageFont.truetype("arial.ttf", 11)
            except Exception:
                font = ImageFont.load_default()
            for i, layer in enumerate(self.layers):
                if not self._layer_shown(i):
                    continue
                letter = layer["label"][:1].upper()
                for (devx, devy) in layer["points"]:
                    dx, dy = device_to_display(devx, devy, self.display_w, self.display_h)
                    r = 9
                    draw.ellipse([dx-r-2, dy-r-2, dx+r+2, dy+r+2], fill="white")
                    draw.ellipse([dx-r, dy-r, dx+r, dy+r], fill=layer["color"], outline="black")
                    bbox = draw.textbbox((0, 0), letter, font=font)
                    tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
                    draw.text((dx - tw//2, dy - th//2), letter, fill="black", font=font)
            self.tk_image = ImageTk.PhotoImage(display_img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
            return
        if not DRAG_MODE:
            # Points are stored in device space; convert to the *current*
            # display size each time we draw, so resizing the window
            # always keeps dots aligned with the screenshot.
            points_disp = [
                device_to_display(devx, devy, self.display_w, self.display_h)
                for devx, devy in self.points_dev
            ]
            display_img = draw_dots_on_image(display_img, points_disp)
        self.tk_image = ImageTk.PhotoImage(display_img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        if DRAG_MODE and self.clip_rect_dev:
            dx0, dy0, dx1, dy1 = self.clip_rect_dev
            sx0, sy0 = device_to_display(dx0, dy0, self.display_w, self.display_h)
            sx1, sy1 = device_to_display(dx1, dy1, self.display_w, self.display_h)
            self.canvas.create_rectangle(sx0, sy0, sx1, sy1, outline=ACCENT, width=2)

    # ── Click handlers ────────────────────────────────────────

    def on_left_click(self, event):
        devx, devy = display_to_device(event.x, event.y, self.display_w, self.display_h)
        self.points_dev.append((devx, devy))
        self._redraw()
        self.status_var.set(
            f"Point {len(self.points_dev)} → device ({devx},{devy})  |  "
            f"Total: {len(self.points_dev)}"
        )

    def on_right_click(self, event):
        self.undo_point()

    # ── Multi-layer mode ──────────────────────────────────────
    def _build_layer_panel(self):
        """Top-right panel: a radio per layer picks the ONE you're pinning (and
        shows just that layer), plus a 'View all' checkbox to show every layer."""
        panel = tk.Frame(self.canvas, bg="#16213e", bd=1, relief="solid")
        panel.place(relx=1.0, x=-8, y=8, anchor="ne")
        tk.Label(panel, text="LAYER  (pick one to pin)", bg="#16213e", fg="#9fb3c8",
                 font=("Consolas", 8, "bold")).pack(anchor="w", padx=6, pady=(4, 2))
        self.active_intvar = tk.IntVar(value=0)
        self.view_all_var  = tk.BooleanVar(value=False)
        self.layer_count_vars = []
        self.layer_name_labels = []
        for i, layer in enumerate(self.layers):
            row = tk.Frame(panel, bg="#16213e", cursor="hand2")
            row.pack(fill="x", padx=6, pady=1)
            tk.Radiobutton(row, variable=self.active_intvar, value=i, bg="#16213e",
                           activebackground="#16213e", selectcolor="#0d0d1a",
                           highlightthickness=0, bd=0,
                           command=lambda idx=i: self._set_active(idx)).pack(side="left")
            tk.Label(row, text="●", fg=layer["color"], bg="#16213e",
                     font=("Consolas", 12)).pack(side="left")
            cvar = tk.StringVar()
            self.layer_count_vars.append(cvar)
            lbl = tk.Label(row, textvariable=cvar, fg="#e0e0e0", bg="#16213e",
                           font=("Consolas", 9), cursor="hand2")
            lbl.pack(side="left", padx=(2, 8))
            lbl.bind("<Button-1>", lambda e, idx=i: (self.active_intvar.set(idx), self._set_active(idx)))
            self.layer_name_labels.append(lbl)

        tk.Frame(panel, bg="#2a3a55", height=1).pack(fill="x", padx=6, pady=(3, 3))
        tk.Checkbutton(panel, text="View all", variable=self.view_all_var,
                       bg="#16213e", fg="#e0e0e0", activebackground="#16213e",
                       activeforeground="#e0e0e0", selectcolor="#0d0d1a",
                       highlightthickness=0, bd=0, font=("Consolas", 9),
                       command=self._redraw).pack(anchor="w", padx=6)
        self.active_var = tk.StringVar()
        tk.Label(panel, textvariable=self.active_var, bg="#16213e", fg=ACCENT,
                 font=("Consolas", 9, "bold")).pack(anchor="w", padx=6, pady=(2, 5))
        self._update_layer_labels()
        self._set_active(0)

    def _layer_shown(self, idx):
        """A layer is drawn if 'View all' is on, or it's the active one."""
        return self.view_all_var.get() or idx == self.active_idx

    def _update_layer_labels(self):
        """Refresh each layer's 'Name n/target' text, green when it matches."""
        for i, layer in enumerate(self.layers):
            n, c = len(layer["points"]), layer["count"]
            self.layer_count_vars[i].set(f"{layer['label']} {n}/{c}" if c else f"{layer['label']} ({n})")
            done = c and n == c
            self.layer_name_labels[i].configure(fg="#39FF14" if done else "#e0e0e0")

    def _set_active(self, idx):
        if not (0 <= idx < len(self.layers)):
            return
        self.active_idx = idx
        self.active_intvar.set(idx)
        self.active_var.set(f"► pinning: {self.layers[idx]['label']}")
        self._redraw()

    def on_layer_click(self, event):
        if not self.layers:
            return
        layer = self.layers[self.active_idx]
        if layer["count"] and len(layer["points"]) >= layer["count"]:
            self.status_var.set(f"{layer['label']}: already at {layer['count']} — Undo to change one.")
            return
        devx, devy = display_to_device(event.x, event.y, self.display_w, self.display_h)
        layer["points"].append((devx, devy))
        self._redraw()
        self._update_layer_labels()
        self.status_var.set(f"{layer['label']}: +({devx},{devy})  {len(layer['points'])}/{layer['count'] or '∞'}")

    # ── Clip-mode drag handlers ───────────────────────────────
    def on_clip_press(self, event):
        self._drag_start = (event.x, event.y)
        self.clip_rect_dev = None
        self._redraw()

    def on_clip_drag(self, event):
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        dx0, dy0 = display_to_device(min(x0, event.x), min(y0, event.y), self.display_w, self.display_h)
        dx1, dy1 = display_to_device(max(x0, event.x), max(y0, event.y), self.display_w, self.display_h)
        self.clip_rect_dev = (dx0, dy0, dx1, dy1)
        self._redraw()
        self.status_var.set(f"Selection (device px): {self.clip_rect_dev}")

    def on_clip_release(self, event):
        self._drag_start = None

    def undo_point(self):
        if MULTI_MODE:
            layer = self.layers[self.active_idx]
            if layer["points"]:
                removed = layer["points"].pop()
                self._redraw()
                self._update_layer_labels()
                self.status_var.set(f"{layer['label']}: removed {removed}  ({len(layer['points'])} left)")
            return
        if DRAG_MODE:
            self.clip_rect_dev = None
            self._redraw()
            self.status_var.set("Selection cleared.")
            return
        if self.points_dev:
            removed = self.points_dev.pop()
            self._redraw()
            self.status_var.set(
                f"Removed {removed}  |  Total: {len(self.points_dev)}"
            )

    def clear_points(self):
        if MULTI_MODE:
            layer = self.layers[self.active_idx]
            layer["points"].clear()
            self._redraw()
            self._update_layer_labels()
            self.status_var.set(f"{layer['label']}: cleared")
            return
        if DRAG_MODE:
            self.clip_rect_dev = None
            self._redraw()
            self.status_var.set("Selection cleared.")
            return
        self.points_dev.clear()
        self._redraw()
        self.status_var.set("All points cleared.")

    # ── Save / load ───────────────────────────────────────────

    def _validate_box(self):
        """Shared validation for the two drag modes. Returns the box or None."""
        if not self.clip_rect_dev:
            messagebox.showwarning("No selection", "Drag a box around the number before saving.")
            return None
        dx0, dy0, dx1, dy1 = self.clip_rect_dev
        if (dx1 - dx0) < 4 or (dy1 - dy0) < 4:
            messagebox.showwarning("Too small", "That box is too small — drag a larger one.")
            return None
        if self.base_image is None:
            messagebox.showwarning("No screenshot", "No screenshot yet — press Refresh first.")
            return None
        return (dx0, dy0, dx1, dy1)

    def _clear_sentinel(self):
        if SENTINEL_FILE and os.path.exists(SENTINEL_FILE):
            try:
                os.remove(SENTINEL_FILE)
            except OSError:
                pass

    def save_and_close(self):
        if MULTI_MODE:
            # Warn if any layer with a target count doesn't match it yet.
            mismatched = [f"{l['label']} {len(l['points'])}/{l['count']}"
                          for l in self.layers if l["count"] and len(l["points"]) != l["count"]]
            if mismatched:
                if not messagebox.askyesno(
                        "Counts don't match",
                        "These layers don't match the count set in Configuration:\n  "
                        + "\n  ".join(mismatched)
                        + "\n\nSave anyway?"):
                    return
            if COMBINE_FILE:
                # Concatenate all layers in order into one "slots" file.
                combined = []
                for layer in self.layers:
                    combined.extend(layer["points"])
                data = {"device_resolution": [DEVICE_W, DEVICE_H],
                        "slots": [{"x": x, "y": y} for x, y in combined]}
                with open(COMBINE_FILE, "w") as f:
                    json.dump(data, f, indent=2)
                self._clear_sentinel()
                messagebox.showinfo("Saved",
                                    f"✔  {len(combined)} slot(s) saved to:\n{os.path.basename(COMBINE_FILE)}"
                                    "\n\nYou can close this window.")
                self.root.destroy()
                return
            wrote = []
            for layer in self.layers:
                if not layer["points"]:
                    continue   # don't overwrite a file with an empty layer
                data = {"device_resolution": [DEVICE_W, DEVICE_H],
                        "points": [{"x": x, "y": y} for x, y in layer["points"]]}
                with open(layer["file"], "w") as f:
                    json.dump(data, f, indent=2)
                wrote.append(f"{layer['label']} ({len(layer['points'])})")
            self._clear_sentinel()
            messagebox.showinfo("Saved",
                                "Saved:\n  " + ("\n  ".join(wrote) if wrote else "nothing") +
                                "\n\nYou can close this window.")
            self.root.destroy()
            return
        if RECT_MODE:
            box = self._validate_box()
            if box is None:
                return
            with open(RECT_OUTPUT, "w") as f:
                json.dump({"roi": list(box), "device_resolution": [DEVICE_W, DEVICE_H]}, f, indent=2)
            self._clear_sentinel()
            messagebox.showinfo("Saved",
                                f"✔  OCR box {box} saved to:\n{RECT_OUTPUT}\n\nYou can close this window.")
            self.root.destroy()
            return
        if CLIP_MODE:
            box = self._validate_box()
            if box is None:
                return
            dx0, dy0, dx1, dy1 = box
            self.base_image.crop((dx0, dy0, dx1, dy1)).save(CLIP_OUTPUT)
            self._clear_sentinel()
            messagebox.showinfo(
                "Saved",
                f"✔  Clip ({dx1-dx0}×{dy1-dy0} px) saved to:\n{CLIP_OUTPUT}\n\n"
                "You can close this window."
            )
            self.root.destroy()
            return
        if not self.points_dev:
            messagebox.showwarning("No points",
                                   "Place at least one dot before saving.")
            return
        # Always save with "points" key so app.py can load either file
        data = {
            "device_resolution": [DEVICE_W, DEVICE_H],
            "points": [{"x": x, "y": y} for x, y in self.points_dev]
        }
        with open(POINTS_FILE, "w") as f:
            json.dump(data, f, indent=2)
        
        # ✅ Remove sentinel to signal successful save
        if SENTINEL_FILE and os.path.exists(SENTINEL_FILE):
            try:
                os.remove(SENTINEL_FILE)
            except OSError:
                pass
        
        messagebox.showinfo(
            "Saved",
            f"✔  {len(self.points_dev)} point(s) saved to:\n{POINTS_FILE}\n\n"
            "You can close this window."
        )
        self.root.destroy()

    def _try_load_existing_points(self):
        if DRAG_MODE:
            return   # nothing to pre-load in clip / rect mode
        if not os.path.exists(POINTS_FILE):
            return
        try:
            with open(POINTS_FILE) as f:
                data = json.load(f)
            # Support both "points" and "slots" keys
            raw_pts = data.get("points", data.get("slots", []))
            for pt in raw_pts:
                devx, devy = pt["x"], pt["y"]
                self.points_dev.append((devx, devy))
        except Exception as e:
            print(f"[overlay] Could not load existing points: {e}")


# ── Entry point ───────────────────────────────────────────────

def run():
    """Open the overlay window (called directly by the frozen exe's dispatch)."""
    root = tk.Tk()
    DeployOverlay(root)
    root.mainloop()


if __name__ == "__main__":
    run()