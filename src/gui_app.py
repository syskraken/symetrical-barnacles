import customtkinter as ctk
import threading
import sys
import os
import io
import json
import time
import re
import webbrowser
import shutil
from PIL import Image, ImageTk, ImageDraw
import subprocess
from tkinter import messagebox, Canvas, Scrollbar
from telemetry_client import TelemetryClient

# Suppress terminal flicker on Windows — every subprocess call (ADB, overlay,
# app.py itself) uses this flag so no black cmd window ever flashes on screen.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── Auto-update (GitHub Releases) ─────────────────────────────
# Bump APP_VERSION on every release you publish, and set GITHUB_REPO to your
# "owner/repo". The app compares against the latest release's tag (e.g. v1.2.0)
# and offers a one-click self-update of the exe.
APP_VERSION = "1.0.0"
GITHUB_REPO = "syskraken/symetrical-barnacles"
UPDATE_API  = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

# ── Frozen-exe subprocess helper ──────────────────────────────
# When PyInstaller compiles everything into KrakenPrime.exe, sys.executable
# points to KrakenPrime.exe — NOT to python.exe.  Calling
#   subprocess.Popen([sys.executable, "app.py"])
# re-launches the whole GUI exe instead of running the script.
#
# _python_exe()   → real python.exe path (for running .py scripts)
# _script_path(f) → absolute path to a bundled .py file (works frozen + dev)
#
def _python_exe() -> str:
    """Return the real python.exe, even when running as a PyInstaller .exe."""
    if not getattr(sys, "frozen", False):
        return sys.executable          # dev mode: already python.exe

    # Frozen: sys.executable is KrakenPrime.exe — find python.exe next to it
    # or walk common install paths.
    exe_dir = os.path.dirname(sys.executable)

    # 1. python.exe sitting next to the compiled exe (cleanest deploy)
    candidate = os.path.join(exe_dir, "python.exe")
    if os.path.isfile(candidate):
        return candidate

    # 2. Search registry for Python 3.11 install dir
    try:
        import winreg
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for subkey in (
                r"SOFTWARE\Python\PythonCore\3.11\InstallPath",
                r"SOFTWARE\WOW6432Node\Python\PythonCore\3.11\InstallPath",
            ):
                try:
                    key = winreg.OpenKey(root, subkey)
                    install_dir, _ = winreg.QueryValueEx(key, "ExecutablePath")
                    winreg.CloseKey(key)
                    if os.path.isfile(install_dir):
                        return install_dir
                except Exception:
                    pass
    except Exception:
        pass

    # 3. Common fixed paths
    for path in [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "Python311", "python.exe"),
        r"C:\Python311\python.exe",
        r"C:\Program Files\Python311\python.exe",
    ]:
        if os.path.isfile(path):
            return path

    # 4. Last resort: whatever "python" resolves to in PATH
    import shutil
    found = shutil.which("python")
    return found if found else "python"


def _script_path(filename: str) -> str:
    """Return absolute path to a bundled .py script.

    When frozen, PyInstaller extracts data files to sys._MEIPASS (the temp
    folder).  In dev mode the scripts live next to gui_app.py.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)

BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0])) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # dev: src/ -> project root


def _script_path(filename: str) -> str:
    """Return absolute path to a bundled .py script.

    When frozen, PyInstaller extracts data files to sys._MEIPASS (the temp
    folder).  In dev mode the scripts live next to gui_app.py.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


def _bot_cmd(*args):
    """Command to run the farm/setup loop. Frozen: relaunch THIS exe with a
    dispatch flag (no external Python needed). Dev: external python + app.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-bot", *args]
    return [_python_exe(), _script_path("app.py"), *args]


def _overlay_cmd():
    """Command to run the pin overlay (see _bot_cmd)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-overlay"]
    return [_python_exe(), _script_path("deploy_overlay.py")]


def _resolve_adb():
    if sys.platform != "win32":
        return "adb"
    dest_dir = os.path.join(os.environ.get("LOCALAPPDATA", BASE_DIR), "KrakenPrime", "adb")
    dest_adb = os.path.join(dest_dir, "adb.exe")
    if os.path.isfile(dest_adb):
        return dest_adb
    # adb ships in _MEIPASS (frozen), bin/ (dev source), or flat next to the exe.
    src_candidates = [getattr(sys, "_MEIPASS", None), os.path.join(BASE_DIR, "bin"), BASE_DIR]
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
            print(f"  [!] Could not extract bundled adb.exe: {e}")
    for cand in (os.path.join(BASE_DIR, "bin", "adb.exe"), os.path.join(BASE_DIR, "adb.exe")):
        if os.path.isfile(cand):
            return cand
    return "adb"


ADB = _resolve_adb()


# Set theme and appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Brand theme ──────────────────────────────────────────────
ACCENT       = "#df7d59"   # primary brand color
ACCENT_HOVER = "#c5663f"   # darker, for hover states
ACCENT_DARK  = "#7a4530"   # dark filled variant (secondary buttons)
ACCENT_SOFT  = "#e8a87c"   # lighter tint, for secondary accents/cards

class ProfessionalCoCBot(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("KRAKEN PRIME")
        self.geometry("1100x700")
        self.resizable(False, False)

        self.frozen = getattr(sys, "frozen", False)
        # dev: gui_app.py is in src/, so the project root is one level up.
        self.app_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if self.frozen \
            else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.resource_dir = getattr(sys, "_MEIPASS", self.app_dir)
        # Icons: bundled at the _MEIPASS root when frozen; in assets/ in dev.
        self.asset_dir = self.resource_dir if self.frozen else os.path.join(self.app_dir, "assets")
        # All runtime state (config, pinned points, captured clips) lives in data/.
        self.data_dir = os.path.join(self.app_dir, "data")
        os.makedirs(self.data_dir, exist_ok=True)
        os.chdir(self.app_dir)

        self._set_window_icon()

        # Configuration
        self.config_file = os.path.join(self.data_dir, "config.json")
        self.overlay_script = _script_path("deploy_overlay.py")
        self.config = self.load_initial_config()
        
        # State variables
        self.attack_count = 0
        self.start_time = time.time()
        self.bot_process = None
        self.stop_event = threading.Event()
        self.active_preset_id = None  # e.g. "preset1" once the bot logs which preset it's using

        # Layout setup
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._create_sidebar()
        self._create_main_content()
        
        # Initialize button states
        self._update_button_states()
        
        # Initialize with Dashboard
        self.select_frame_by_name("dashboard")
        self.telemetry = TelemetryClient(self, self.data_dir)
        self.telemetry.start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-update: clean up a leftover post-update file, then check in the
        # background (only meaningful for the compiled exe).
        self._update_info = None
        self._cleanup_old_exe()
        if self.frozen:
            threading.Thread(target=self._check_for_update, daemon=True).start()

    def _on_close(self):
        self.telemetry.stop()
        self.destroy()

    # ── Auto-update (GitHub Releases) ─────────────────────────────
    @staticmethod
    def _version_tuple(v):
        """'v1.2.10' -> (1, 2, 10) for comparison; non-numeric parts -> 0."""
        nums = re.findall(r"\d+", str(v))
        return tuple(int(n) for n in nums) if nums else (0,)

    def _cleanup_old_exe(self):
        """Remove the renamed previous exe left behind by a prior update."""
        try:
            if self.frozen:
                old = os.path.join(os.path.dirname(sys.executable), "KrakenPrime.old.exe")
                if os.path.exists(old):
                    os.remove(old)
        except Exception:
            pass

    def _check_for_update(self):
        """Query GitHub for the latest release; show the button if it's newer."""
        try:
            import requests
            r = requests.get(UPDATE_API, timeout=8,
                             headers={"Accept": "application/vnd.github+json"})
            if r.status_code != 200:
                return
            data = r.json()
            tag = str(data.get("tag_name", "")).lstrip("vV")
            if not tag or self._version_tuple(tag) <= self._version_tuple(APP_VERSION):
                return
            asset = next((a for a in data.get("assets", [])
                          if str(a.get("name", "")).lower().endswith(".exe")), None)
            if not asset:
                return
            self._update_info = {
                "version": tag,
                "url": asset["browser_download_url"],
                "size": int(asset.get("size", 0) or 0),
                "notes": (data.get("body") or "").strip(),
            }
            self.after(0, self._show_update_button)
        except Exception as e:
            print(f"[update] check failed: {e}")

    def _show_update_button(self):
        info = self._update_info or {}
        self.update_btn.configure(text=f"⬆ Update to v{info.get('version','')}")
        self.update_btn.pack(fill="x", pady=(6, 2), before=self.version_label)

    def on_update_clicked(self):
        info = self._update_info
        if not info:
            return
        mb = f"{info['size'] / 1_000_000:.0f} MB" if info.get("size") else "the new version"
        notes = ("\n\nWhat's new:\n" + info["notes"][:600]) if info.get("notes") else ""
        if not messagebox.askyesno("Update available",
                                   f"Download and install v{info['version']} ({mb})?"
                                   "\nThe app will restart automatically." + notes):
            return
        self.update_btn.configure(state="disabled", text="Downloading… 0%")
        threading.Thread(target=self._do_update, args=(info,), daemon=True).start()

    def _do_update(self, info):
        """Download the new exe next to the current one, swap via rename, restart."""
        try:
            import requests
            cur = sys.executable
            d = os.path.dirname(cur)
            new = os.path.join(d, "KrakenPrime.new.exe")
            old = os.path.join(d, "KrakenPrime.old.exe")

            with requests.get(info["url"], stream=True, timeout=60) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("content-length", info.get("size") or 0))
                done = 0
                with open(new, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=262144):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(done * 100 / total)
                            self.after(0, lambda p=pct: self.update_btn.configure(text=f"Downloading… {p}%"))

            if info.get("size") and os.path.getsize(new) != info["size"]:
                raise IOError("downloaded size mismatch — aborting")

            # Rename-swap: a running exe can't be overwritten, but it CAN be renamed.
            if os.path.exists(old):
                os.remove(old)
            os.rename(cur, old)     # move running exe aside
            os.rename(new, cur)     # put the new one in its place
            subprocess.Popen([cur], creationflags=_NO_WINDOW)
            self.after(0, self._on_close)
        except Exception as e:
            print(f"[update] failed: {e}")
            self.after(0, lambda: (self.update_btn.configure(state="normal"),
                                   self._show_update_button(),
                                   messagebox.showerror("Update failed",
                                                        f"Could not update:\n{e}\n\nTry again later.")))

    def _set_window_icon(self):
        icon_path = os.path.join(self.asset_dir, "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
                return
            except Exception:
                pass

        # Fallback: use a bundled PNG if available
        png_icon = os.path.join(self.asset_dir, "icon.png")
        if os.path.exists(png_icon):
            try:
                img = Image.open(png_icon)
                self.iconphoto(True, ImageTk.PhotoImage(img))
            except Exception:
                pass

    def _load_icon_image(self, size=(28, 28)):
        """Loads icon.png as a CTkImage for use in labels (replaces emoji branding)."""
        icon_path = os.path.join(self.asset_dir, "icon.png")
        if not os.path.exists(icon_path):
            return None
        try:
            img = Image.open(icon_path).convert("RGBA")
            img = img.resize(size, Image.LANCZOS)
            return ctk.CTkImage(light_image=img, dark_image=img, size=size)
        except Exception:
            return None

    def _create_sidebar(self):
        self.sidebar_frame = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#1a1c1e")
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(8, weight=1)

        self.logo_icon_image = self._load_icon_image((26, 26))
        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="  KRAKEN PRIME",
                                     image=self.logo_icon_image, compound="left",
                                     font=ctk.CTkFont(size=22, weight="bold"), text_color=ACCENT, anchor="w")
        self.logo_label.grid(row=0, column=0, padx=20, pady=(30, 8), sticky="w")

        self.sidebar_brand = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.sidebar_brand.grid(row=1, column=0, padx=20, pady=(0, 10), sticky="w")
        ctk.CTkLabel(self.sidebar_brand, text="Clash of Clans Bot", font=ctk.CTkFont(size=10), anchor="w", height=14).pack(anchor="w", pady=0)
        discord_link = ctk.CTkLabel(self.sidebar_brand, text="Discord: https://discord.gg/jV7ymDtH7F", font=ctk.CTkFont(size=10), text_color="#8ab4f8", anchor="w", cursor="hand2", height=14)
        discord_link.pack(anchor="w", pady=0)
        discord_link.bind("<Button-1>", lambda e: self.open_url("https://discord.gg/jV7ymDtH7F"))
        
        github_link = ctk.CTkLabel(self.sidebar_brand, text="Github: https://github.com/syskraken", font=ctk.CTkFont(size=10), text_color="#8ab4f8", anchor="w", cursor="hand2", height=14)
        github_link.pack(anchor="w", pady=0)
        github_link.bind("<Button-1>", lambda e: self.open_url("https://github.com/syskraken"))

        # Navigation Buttons
        self.dashboard_button = self._create_nav_button("Dashboard", "dashboard", 2)
        self.config_button = self._create_nav_button("Configuration", "config", 3)
        self.upgrade_button = self._create_nav_button("Auto Upgrade", "upgrade", 4)
        self.preview_button = self._create_nav_button("Live Preview", "preview", 5)
        self.history_button = self._create_nav_button("Raid History", "history", 6)
        self.developer_button = self._create_nav_button("Donation", "developer", 7)

        # Control Section
        self.control_frame = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.control_frame.grid(row=8, column=0, padx=20, pady=20, sticky="ew")

        # Glow border frame for START BOT button
        self.start_btn_glow_frame = ctk.CTkFrame(self.control_frame, fg_color=ACCENT, corner_radius=8)
        self.start_btn_glow_frame.pack(fill="x", pady=5, padx=0)

        self.start_btn = ctk.CTkButton(self.start_btn_glow_frame, text="START BOT", command=self.toggle_bot,
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER, height=40, font=ctk.CTkFont(weight="bold"))
        self.start_btn.pack(fill="x", padx=2, pady=2)

        self.refresh_btn = ctk.CTkButton(self.control_frame, text="🔄 REFRESH", command=self.refresh_ui,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, height=35, font=ctk.CTkFont(weight="bold"))
        self.refresh_btn.pack(fill="x", pady=5)

        # Update button — hidden until a newer release is found.
        self.update_btn = ctk.CTkButton(self.control_frame, text="", command=self.on_update_clicked,
                                        fg_color="#2e8b57", hover_color="#3aa76a", height=35,
                                        font=ctk.CTkFont(weight="bold"))
        self.version_label = ctk.CTkLabel(self.control_frame, text=f"v{APP_VERSION}",
                                          text_color="gray50", font=ctk.CTkFont(size=10))
        self.version_label.pack(pady=(2, 0))

        self.status_label = ctk.CTkLabel(self.sidebar_frame, text="● SYSTEM READY", text_color="#FFD700",
                                       font=ctk.CTkFont(size=12, weight="bold"))
        self.status_label.grid(row=9, column=0, padx=20, pady=(0, 10))

        self.setup_warning = ctk.CTkLabel(self.sidebar_frame, text="⚠️ SETUP INCOMPLETE", text_color="#dc3545",
                                        font=ctk.CTkFont(size=10, weight="bold"))
        self.setup_warning.grid(row=10, column=0, padx=20, pady=(0, 20))
        
        self.setup_complete_label = ctk.CTkLabel(self.sidebar_frame, text="✅ SETUP COMPLETE - READY TO FARM!", text_color=ACCENT,
                                                font=ctk.CTkFont(size=10, weight="bold"))
        # The warning is managed by _update_button_states()
        
        # Glow effect tracking
        self.glow_active = False
        self.glow_direction = 1  # 1 for increasing, -1 for decreasing

    def _create_nav_button(self, text, name, row):
        btn = ctk.CTkButton(self.sidebar_frame, corner_radius=8, height=40, border_spacing=10, text=text,
                           fg_color="transparent", text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                           anchor="w", command=lambda: self.select_frame_by_name(name))
        btn.grid(row=row, column=0, sticky="ew", padx=15, pady=5)
        return btn

    def _create_main_content(self):
        # Dashboard Frame
        self.dashboard_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.dashboard_frame.grid_columnconfigure((0, 1), weight=1)
        self.dashboard_frame.grid_rowconfigure(1, weight=1)
        
        # --- Stats Cards ---
        self.card_attacks = self._create_stat_card(self.dashboard_frame, "TOTAL RAIDS", "0", ACCENT, 0, 0)
        self.card_runtime = self._create_stat_card(self.dashboard_frame, "UPTIME", "00:00:00", ACCENT_SOFT, 0, 1)

        # --- System Logs (replaces Current Operation card) ---
        self.task_frame = ctk.CTkFrame(self.dashboard_frame)
        self.task_frame.grid(row=1, column=0, columnspan=2, padx=20, pady=(0, 20), sticky="nsew")
        self.task_frame.grid_rowconfigure(1, weight=1)
        self.task_frame.grid_columnconfigure(0, weight=1)
        self.task_title = ctk.CTkLabel(self.task_frame, text="SYSTEM LOGS", font=ctk.CTkFont(size=12, weight="bold"))
        self.task_title.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        self.log_textbox = ctk.CTkTextbox(self.task_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_textbox.grid(row=1, column=0, padx=15, pady=(0, 15), sticky="nsew")

        # Configuration Frame
        self.config_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.config_frame.grid_rowconfigure(0, weight=1)
        self.config_frame.grid_columnconfigure(0, weight=1)

        self.config_canvas = Canvas(self.config_frame, bg="#1a1c1e", highlightthickness=0)
        self.config_canvas.grid(row=0, column=0, sticky="nsew")

        self.config_scrollbar = Scrollbar(self.config_frame, orient="vertical", command=self.config_canvas.yview)
        self.config_scrollbar.grid(row=0, column=1, sticky="ns")

        self.config_canvas.configure(yscrollcommand=self.config_scrollbar.set)
        self.config_content = ctk.CTkFrame(self.config_canvas, fg_color="transparent")
        self.config_canvas.create_window((0, 0), window=self.config_content, anchor="nw")
        self.config_content.grid_columnconfigure(0, weight=1)
        self.config_content.grid_columnconfigure(1, weight=1)

        self.config_canvas.bind("<Configure>", lambda e: self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all")))
        self.config_content.bind("<Configure>", lambda e: self.config_canvas.configure(scrollregion=self.config_canvas.bbox("all")))
        self.config_canvas.bind_all("<MouseWheel>", lambda e: self.config_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        self._setup_config_tabs()

        # Auto Upgrade Frame
        self._setup_upgrade_tab()

        # Preview Frame
        self.preview_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.preview_label = ctk.CTkLabel(self.preview_frame, text="Live Screen Preview", font=ctk.CTkFont(size=20, weight="bold"))
        self.preview_label.pack(pady=20)
        self.screen_canvas = ctk.CTkLabel(self.preview_frame, text="Click  📷 Refresh Screenshot  to grab a live view.", fg_color="#000", width=800, height=450)
        self.screen_canvas.pack(padx=20, pady=(0, 10))

        # Refresh screenshot button
        self.refresh_screenshot_btn = ctk.CTkButton(
            self.preview_frame,
            text="📷  Refresh Screenshot",
            command=self.refresh_preview_once,
            fg_color="#0f3460", hover_color="#e94560",
            font=ctk.CTkFont(size=13, weight="bold"),
            height=36
        )
        self.refresh_screenshot_btn.pack(pady=(0, 8))

        # --- Overlay legend / toggles ---
        self.overlay_vars = {
            "slots":  ctk.BooleanVar(value=True),
            "deploy": ctk.BooleanVar(value=True),
            "hero":   ctk.BooleanVar(value=True),
            "rage":   ctk.BooleanVar(value=True),
        }
        legend_frame = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        legend_frame.pack(padx=20, pady=(0, 4))

        self._add_overlay_toggle(legend_frame, "slots",  "Troop Slots",  "#00BFFF", 0)
        self._add_overlay_toggle(legend_frame, "deploy", "Troops",       "#39FF14", 1)
        self._add_overlay_toggle(legend_frame, "hero",   "Heroes",       "#3fa7ff", 2)
        self._add_overlay_toggle(legend_frame, "rage",   "Spells",       "#FF3B3B", 3)

        # Second row: shown only in Preset Mode, one swatch pair per active preset
        self.preset_legend_frame = ctk.CTkFrame(self.preview_frame, fg_color="transparent")
        self.preset_legend_frame.pack(padx=20, pady=(0, 10))

        # History Frame
        self.history_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.history_list = ctk.CTkTextbox(self.history_frame, font=ctk.CTkFont(family="Consolas", size=12))
        self.history_list.pack(fill="both", expand=True, padx=20, pady=20)

        # Developer / Donation Frame
        self.developer_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        dev_title = ctk.CTkLabel(self.developer_frame, text="Support the Developer",
                                  font=ctk.CTkFont(size=20, weight="bold"))
        dev_title.pack(anchor="w", padx=20, pady=(20, 5))

        dev_subtitle = ctk.CTkLabel(self.developer_frame,
                                     text="If Kraken Prime has been useful to you, consider supporting development.",
                                     font=ctk.CTkFont(size=12), text_color="gray70", anchor="w", justify="left")
        dev_subtitle.pack(anchor="w", padx=20, pady=(0, 20))

        donate_card = ctk.CTkFrame(self.developer_frame, corner_radius=10)
        donate_card.pack(fill="x", padx=20, pady=(0, 20))

        ctk.CTkLabel(donate_card, text="☕ Buy me a coffee", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(15, 5))
        coffee_link = ctk.CTkLabel(donate_card, text="https://ko-fi.com/franklinmarshall",
                     font=ctk.CTkFont(size=12), text_color="#8ab4f8", cursor="hand2", anchor="w")
        coffee_link.pack(anchor="w", padx=15, pady=(0, 5))
        coffee_link.bind("<Button-1>", lambda e: self.open_url("https://ko-fi.com/franklinmarshall"))

        
        ctk.CTkLabel(donate_card, text="Paypal", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(15, 5))
        coffee_link = ctk.CTkLabel(donate_card, text="https://www.paypal.com/paypalme/FranklinTripole?locale.x=en_US&country.x=PH",
                     font=ctk.CTkFont(size=12), text_color="#8ab4f8", cursor="hand2", anchor="w")
        coffee_link.pack(anchor="w", padx=15, pady=(0, 5))
        coffee_link.bind("<Button-1>", lambda e: self.open_url("https://www.paypal.com/paypalme/FranklinTripole?locale.x=en_US&country.x=PH"))


        ctk.CTkLabel(donate_card, text="Gcash", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 5))
        ctk.CTkLabel(donate_card, text="09271272799",
                     font=ctk.CTkFont(family="Consolas", size=12), text_color="#8ab4f8", anchor="w").pack(
            anchor="w", padx=15)
        ctk.CTkLabel(donate_card, text="Franklin Tripole",
                     font=ctk.CTkFont(family="Consolas", size=12), text_color="#8ab4f8", anchor="w").pack(
            anchor="w", padx=15, pady=(0, 5))

        ctk.CTkLabel(donate_card, text="Email: nzeus624@gmail.com", font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=15, pady=(10, 5))

    

        self.thanks_icon_image = self._load_icon_image((16, 16))
        thanks_row = ctk.CTkFrame(self.developer_frame, fg_color="transparent")
        thanks_row.pack(anchor="w", padx=20, pady=(0, 20))
        ctk.CTkLabel(thanks_row, text="", image=self.thanks_icon_image).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(thanks_row, text="Thanks for your support!",
                     font=ctk.CTkFont(size=12, slant="italic"), text_color="gray60").pack(side="left")

    def _create_stat_card(self, parent, title, value, color, row, col):
        card = ctk.CTkFrame(parent, height=120)
        card.grid(row=row, column=col, padx=15, pady=20, sticky="nsew")
        title_lbl = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=12, weight="bold"), text_color="gray70")
        title_lbl.pack(pady=(20, 0))
        val_lbl = ctk.CTkLabel(card, text=value, font=ctk.CTkFont(size=32, weight="bold"), text_color=color)
        val_lbl.pack(pady=(0, 20))
        return val_lbl

    def _add_overlay_toggle(self, parent, key, label, color, col):
        chip = ctk.CTkFrame(parent, fg_color="transparent")
        chip.grid(row=0, column=col, padx=10)
        swatch = ctk.CTkLabel(chip, text="", width=14, height=14, fg_color=color, corner_radius=3)
        swatch.pack(side="left", padx=(0, 6))
        cb = ctk.CTkCheckBox(chip, text=label, variable=self.overlay_vars[key],
                              font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18,
                              command=self.refresh_preview_once)
        cb.pack(side="left")

    @staticmethod
    def _rgb_to_hex(rgb):
        return "#{:02X}{:02X}{:02X}".format(*rgb)

    def _add_swatch_label(self, parent, color_rgb, text, col):
        chip = ctk.CTkFrame(parent, fg_color="transparent")
        chip.grid(row=0, column=col, padx=6)
        swatch = ctk.CTkLabel(chip, text="", width=12, height=12,
                               fg_color=self._rgb_to_hex(color_rgb), corner_radius=3)
        swatch.pack(side="left", padx=(0, 4))
        ctk.CTkLabel(chip, text=text, font=ctk.CTkFont(size=11), text_color="gray70").pack(side="left")

    def _update_preset_legend(self):
        """Rebuild the per-preset color key — only shown when Preset Mode is enabled."""
        for widget in self.preset_legend_frame.winfo_children():
            widget.destroy()

        if not bool(self.config.get("deploy_preset_enabled", False)):
            return

        if self.active_preset_id:
            preset_ids = [self.active_preset_id]
            suffix = " (in use)"
        else:
            raw_order = self.config.get("deploy_preset_order", "preset1,preset2,preset3")
            preset_ids = [p.strip() for p in str(raw_order).split(",") if p.strip()] or ["preset1"]
            suffix = ""

        col = 0
        for preset_id in preset_ids:
            try:
                slot_num = int(preset_id.replace("preset", ""))
            except ValueError:
                continue
            colors = self.PRESET_COLORS.get(slot_num, self.PRESET_COLORS[1])
            self._add_swatch_label(self.preset_legend_frame, colors["deploy"], f"Preset {slot_num} Deploy{suffix}", col)
            col += 1
            self._add_swatch_label(self.preset_legend_frame, colors["rage"], f"Preset {slot_num} Rage{suffix}", col)
            col += 1


    def _setup_config_tabs(self):
        # We'll use a dictionary to store entries for easy saving
        self.entries = {}
        parent = self.config_content

        troops_panel = ctk.CTkFrame(parent, fg_color="transparent")
        troops_panel.grid(row=0, column=0, padx=(20, 10), pady=(0, 20), sticky="nsew")
        troops_panel.grid_columnconfigure(0, weight=1)
        troops_panel.grid_columnconfigure(1, weight=1)

        spells_panel = ctk.CTkFrame(parent, fg_color="transparent")
        spells_panel.grid(row=0, column=1, padx=(10, 20), pady=(0, 20), sticky="nsew")
        spells_panel.grid_columnconfigure(0, weight=1)
        spells_panel.grid_columnconfigure(1, weight=1)

        self._build_troops_panel(troops_panel)
        self._build_spells_panel(spells_panel)

        self._add_section_label(parent, "Loot & Targets", 6, 0, span=2)
        loot_frame = ctk.CTkFrame(parent, fg_color="transparent")
        loot_frame.grid(row=7, column=0, columnspan=2, sticky="ew")
        loot_frame.grid_columnconfigure(0, weight=1)
        loot_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(loot_frame, text="Minimum Gold Target").grid(row=0, column=0, padx=20, pady=10, sticky="w")
        min_gold_entry = ctk.CTkEntry(loot_frame, width=220)
        min_gold_entry.grid(row=0, column=1, padx=(10, 20), pady=10, sticky="ew")
        min_gold_entry.insert(0, str(self.config.get("min_gold", "")))
        self.entries["min_gold"] = min_gold_entry

        ctk.CTkLabel(loot_frame, text="Minimum Elixir Target").grid(row=1, column=0, padx=20, pady=10, sticky="w")
        min_elixir_entry = ctk.CTkEntry(loot_frame, width=220)
        min_elixir_entry.grid(row=1, column=1, padx=(10, 20), pady=10, sticky="ew")
        min_elixir_entry.insert(0, str(self.config.get("min_elixir", "")))
        self.entries["min_elixir"] = min_elixir_entry

        # Scout-screen OCR boxes: draw over a target base while scouting.
        ctk.CTkButton(loot_frame, text="SET GOLD SCOUT BOX", fg_color=ACCENT_DARK, hover_color=ACCENT,
                      command=lambda: self.launch_rect_overlay("scout_gold_roi.json", "Box the GOLD amount on the scout screen")
                      ).grid(row=2, column=0, columnspan=2, pady=(6, 4), sticky="ew", padx=20)
        ctk.CTkButton(loot_frame, text="SET ELIXIR SCOUT BOX", fg_color=ACCENT_DARK, hover_color=ACCENT,
                      command=lambda: self.launch_rect_overlay("scout_elixir_roi.json", "Box the ELIXIR amount on the scout screen")
                      ).grid(row=3, column=0, columnspan=2, pady=(0, 6), sticky="ew", padx=20)

        self._add_section_label(parent, "Deployment Presets", 8, 0, span=2)
        preset_enabled_var = ctk.BooleanVar(value=bool(self.config.get("deploy_preset_enabled", False)))
        ctk.CTkLabel(parent, text="Enable preset mode").grid(row=9, column=0, padx=20, pady=10, sticky="w")
        preset_toggle = ctk.CTkSwitch(parent, text="", variable=preset_enabled_var, onvalue=True, offvalue=False)
        preset_toggle.grid(row=9, column=1, padx=(10, 20), pady=10, sticky="w")
        self.entries["deploy_preset_enabled"] = preset_toggle

        self.preset_config_frame = ctk.CTkFrame(parent, fg_color="transparent")
        self.preset_config_frame.grid(row=10, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="nsew")
        self.preset_config_frame.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(self.preset_config_frame, text="Deployment mode:", anchor="w").grid(row=0, column=0, pady=(0, 4), sticky="w")
        preset_mode_var = ctk.StringVar(value=self.config.get("deploy_preset_mode", "sequence"))
        preset_mode_menu = ctk.CTkOptionMenu(self.preset_config_frame, variable=preset_mode_var, values=["sequence", "random"], width=220)
        preset_mode_menu.grid(row=1, column=0, pady=(0, 10), sticky="ew")
        self.entries["deploy_preset_mode"] = preset_mode_menu

        self.preset_list_frame = ctk.CTkFrame(self.preset_config_frame, fg_color="transparent")
        self.preset_list_frame.grid(row=2, column=0, pady=(10, 0), sticky="nsew")

        add_preset_btn = ctk.CTkButton(self.preset_config_frame, text="Add deployment preset", command=self.add_deployment_preset)
        add_preset_btn.grid(row=3, column=0, pady=(8, 4), sticky="ew")

        self._refresh_preset_list()
        self._toggle_preset_config(preset_enabled_var.get())

        preset_toggle.configure(command=lambda: self._toggle_preset_config(preset_enabled_var.get()))

        self._add_section_label(parent, "Advanced", 11, 0, span=2)
        self.run_setup_btn = ctk.CTkButton(parent, text="RUN FULL GUIDED SETUP", command=self.run_setup, fg_color="#6c757d")
        self.run_setup_btn.grid(row=12, column=0, columnspan=2, pady=(10, 5), sticky="ew", padx=20)
        self.edit_troop_slots_btn = ctk.CTkButton(parent, text="EDIT PIN TROOP BAR SLOTS", command=self.edit_troop_bar_slots)
        self.edit_troop_slots_btn.grid(row=13, column=0, columnspan=2, pady=5, sticky="ew", padx=20)
        self.edit_deploy_btn = ctk.CTkButton(parent, text="EDIT DEPLOYMENT (Troops / Heroes / Spells)",
                                             command=self.edit_global_deployment)
        self.edit_deploy_btn.grid(row=14, column=0, columnspan=2, pady=5, sticky="ew", padx=20)
        ctk.CTkButton(parent, text="SAVE ALL SETTINGS", command=self.save_config, fg_color=ACCENT).grid(row=15, column=0, columnspan=2, pady=(15, 5), sticky="ew", padx=20)

        # ── Setup readiness checklist ────────────────────────────
        ready_head = ctk.CTkFrame(parent, fg_color="transparent")
        ready_head.grid(row=16, column=0, columnspan=2, padx=20, pady=(12, 2), sticky="ew")
        ready_head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(ready_head, text="⚡ Setup Readiness", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=ACCENT).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(ready_head, text="🔄 Re-check", width=90, height=26,
                      command=self._refresh_readiness, fg_color=ACCENT_DARK, hover_color=ACCENT,
                      font=ctk.CTkFont(size=11)).grid(row=0, column=1, sticky="e")
        self.readiness_frame = ctk.CTkFrame(parent, fg_color="#141719")
        self.readiness_frame.grid(row=17, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="ew")
        self._refresh_readiness()

        separator = ctk.CTkFrame(parent, height=1, fg_color="gray30")
        separator.grid(row=18, column=0, columnspan=2, padx=20, pady=(10, 5), sticky="ew")

        ctk.CTkLabel(parent, text="Reconfigure", font=ctk.CTkFont(size=12, weight="bold"),
                     text_color="#dc3545").grid(row=19, column=0, columnspan=2, padx=20, pady=(0, 4), sticky="w")

        ctk.CTkButton(
            parent, text="🗑 Reset Deployment Points",
            command=self.reset_all_config,
            fg_color="#7a1a1a", hover_color="#dc3545",
            font=ctk.CTkFont(weight="bold")
        ).grid(row=20, column=0, columnspan=2, pady=(0, 20), sticky="ew", padx=20)

    def _setup_upgrade_tab(self):
        """Auto Upgrade tab — toggles + coordinate pinning. Upgrades run
        opportunistically after each raid whenever gold or elixir is full."""
        self.upgrade_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")

        wrap = ctk.CTkScrollableFrame(self.upgrade_frame, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=10, pady=10)
        wrap.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(wrap, text="Auto Upgrade",
                     font=ctk.CTkFont(size=20, weight="bold"), text_color=ACCENT).grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        ctk.CTkLabel(wrap, anchor="w", justify="left", text_color="gray70",
                     text=("After each raid the bot checks your storages. When gold or elixir\n"
                           "is full it upgrades walls / buildings — using whichever resource is full.\n"
                           "it only spends when something would otherwise overflow."),
                     font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 12))

        # ── Toggles ──────────────────────────────────────────────
        toggles = ctk.CTkFrame(wrap)
        toggles.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 12))
        toggles.grid_columnconfigure(0, weight=1)

        def _switch(row, key, label, default=False):
            ctk.CTkLabel(toggles, text=label, anchor="w").grid(row=row, column=0, sticky="w", padx=15, pady=8)
            var = ctk.BooleanVar(value=bool(self.config.get(key, default)))
            sw = ctk.CTkSwitch(toggles, text="", variable=var, onvalue=True, offvalue=False,
                               command=self._refresh_upgrade_hint)
            sw.grid(row=row, column=1, sticky="e", padx=15, pady=8)
            self.entries[key] = sw

        _switch(0, "auto_upgrade_enabled",   "Enable auto-upgrade",        False)
        _switch(1, "auto_upgrade_walls",     "Upgrade walls",              True)
        _switch(2, "auto_upgrade_buildings", "Upgrade buildings",          True)

        # Live prerequisite hint — updates as you flip the toggles above.
        self.upgrade_hint = ctk.CTkLabel(toggles, text="", anchor="w", justify="left",
                                         font=ctk.CTkFont(size=11), wraplength=560)
        self.upgrade_hint.grid(row=3, column=0, columnspan=2, sticky="w", padx=15, pady=(0, 8))
        self._refresh_upgrade_hint()

        # ── Capture "full" reference clips ───────────────────────
        self._add_section_label(wrap, "Capture \"Full\" References", 3, 0, span=1)
        clips = ctk.CTkFrame(wrap, fg_color="transparent")
        clips.grid(row=4, column=0, sticky="ew", padx=10)
        clips.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(clips, anchor="w", justify="left", text_color="gray60",
                     font=ctk.CTkFont(size=11),
                     text=("Click a button, then DRAW A BOX around the target and Save.\n"
                           "GOLD/ELIXIR: box the resource number while it is FULL.\n"
                           "BUILDERS: box just the free-count digit while ALL builders are busy (0).\n"
                           "The bot matches each clip every cycle.")).grid(
            row=0, column=0, sticky="w", pady=(0, 6))

        # Category colors (fg, hover) so each group of buttons is visually distinct.
        GOLD_C     = ("#c9a227", "#e0b93f")   # gold capture
        ELIXIR_C   = ("#8e44ad", "#a55ec7")   # elixir capture (elixir is purple)
        BUILDERS_C = ("#b5651d", "#d17f2e")   # builders capture
        WALL_C     = ("#4f6b7a", "#6a8ba0")   # wall buttons (stone blue-grey)
        BUILDING_C = ("#3a7d5a", "#4fa578")   # building buttons (green)

        def _clip_btn(row, text, png, title, colors):
            ctk.CTkButton(clips, text=text, fg_color=colors[0], hover_color=colors[1],
                          command=lambda: self.launch_clip_overlay(png, title)).grid(
                row=row, column=0, sticky="ew", pady=4)

        _clip_btn(1, "Capture GOLD full reference",   "gold_full.png",
                  "Clip the GOLD number (while gold is FULL)", GOLD_C)
        _clip_btn(2, "Capture ELIXIR full reference", "elixir_full.png",
                  "Clip the ELIXIR number (while elixir is FULL)", ELIXIR_C)
        _clip_btn(3, "Capture BUILDERS-BUSY reference", "builders_busy.png",
                  "Clip the free-count digit (while ALL builders are BUSY = 0 free)", BUILDERS_C)

        # ── Pinning ──────────────────────────────────────────────
        self._add_section_label(wrap, "Pin Coordinates", 5, 0, span=1)
        pins = ctk.CTkFrame(wrap, fg_color="transparent")
        pins.grid(row=6, column=0, sticky="ew", padx=10)
        pins.grid_columnconfigure(0, weight=1)

        def _pin_btn(row, text, hint, fname, title, colors):
            ctk.CTkButton(pins, text=text, fg_color=colors[0], hover_color=colors[1],
                          command=lambda: self.launch_manual_overlay(fname, title)).grid(
                row=row, column=0, sticky="ew", pady=(8, 0))
            ctk.CTkLabel(pins, text=hint, anchor="w", justify="left", text_color="gray60",
                         font=ctk.CTkFont(size=11)).grid(row=row + 1, column=0, sticky="w", pady=(2, 6))

        _pin_btn(0, "PIN WALL BUTTONS",
                 "Order: 1) Select Row  — then Refresh after selecting a row in-game —  "
                 "2) Upgrade(Gold)  3) Upgrade(Elixir)  4) Confirm (if any).",
                 "wall_buttons.json", "Pin WALL BUTTONS (select row > gold > elixir > confirm)", WALL_C)
        _pin_btn(2, "EDIT WALL TARGETS",
                 "Pin one tile in each wall row/cluster you want upgraded.",
                 "wall_targets.json", "Pin WALL TARGETS (one per row)", WALL_C)
        _pin_btn(4, "PIN BUILDING BUTTONS",
                 "Order: 1) Upgrade  2) Confirm (if a cost dialog appears).",
                 "building_buttons.json", "Pin BUILDING BUTTONS (upgrade > confirm)", BUILDING_C)
        _pin_btn(6, "EDIT BUILDING TARGETS",
                 "Pin each building you want the bot to keep upgrading.",
                 "building_targets.json", "Pin BUILDING TARGETS", BUILDING_C)

        ctk.CTkButton(wrap, text="SAVE ALL SETTINGS", command=self.save_config,
                      fg_color=ACCENT).grid(row=7, column=0, sticky="ew", padx=10, pady=(14, 20))

    def _add_section_label(self, parent, text, row, col=0, span=2):
        ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT).grid(
            row=row, column=col, columnspan=span, padx=20, pady=(4, 8), sticky="w"
        )

    def _toggle_preset_config(self, enabled):
        if hasattr(self, "preset_config_frame"):
            if enabled:
                self.preset_config_frame.grid()
            else:
                self.preset_config_frame.grid_remove()

    def _refresh_preset_list(self):
        for widget in self.preset_list_frame.winfo_children():
            widget.destroy()

        preset_slots = [idx for idx in range(1, 5) if self._preset_exists(idx)]

        if not preset_slots:
            ctk.CTkLabel(self.preset_list_frame, text="No deployment presets saved yet.", text_color="gray70").pack(anchor="w", pady=4)
            return

        for idx in preset_slots:
            has_rage = os.path.exists(os.path.join(self.data_dir, f"rage_preset_{idx}.json"))
            has_hero = os.path.exists(os.path.join(self.data_dir, f"hero_preset_{idx}.json"))
            notes = "troops" + (" + heroes" if has_hero else "") + (" + spells" if has_rage else "")

            row = ctk.CTkFrame(self.preset_list_frame, fg_color="transparent")
            row.pack(fill="x", pady=2)
            row.columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=f"Preset {idx}  ({notes})", anchor="w").grid(
                row=0, column=0, sticky="w")

            ctk.CTkButton(
                row, text="✏ Edit", width=70, height=26,
                fg_color=ACCENT_DARK, hover_color=ACCENT,
                font=ctk.CTkFont(size=11),
                command=lambda i=idx: self._edit_preset(i),
            ).grid(row=0, column=1, padx=(8, 4))

            ctk.CTkButton(
                row, text="🗑 Del", width=70, height=26,
                fg_color="#7a1a1a", hover_color="#dc3545",
                font=ctk.CTkFont(size=11),
                command=lambda i=idx: self._delete_preset(i),
            ).grid(row=0, column=2)

    def _preset_files(self, slot_num):
        """All files that make up one preset (per-troop-type + heroes + per-spell-type + legacy)."""
        n = max(1, min(self._cfg_num("num_troop_slots") or 1, 10))
        m = max(0, min(self._cfg_num("num_spell_slots"), 10))
        files = [f"deploy_preset_{slot_num}_troop_{i}.json" for i in range(1, n + 1)]
        files += [f"spell_preset_{slot_num}_type_{i}.json" for i in range(1, m + 1)]
        files += [f"deploy_preset_{slot_num}.json",   # legacy shared troops file
                  f"hero_preset_{slot_num}.json",
                  f"rage_preset_{slot_num}.json"]      # legacy shared spell file
        return files

    def _preset_exists(self, slot_num):
        return any(os.path.exists(os.path.join(self.data_dir, f)) for f in self._preset_files(slot_num))

    def _edit_preset(self, slot_num):
        threading.Thread(target=self._run_preset_overlay_sequence, args=(slot_num,), daemon=True).start()

    def _delete_preset(self, slot_num):
        if not messagebox.askyesno("Delete Preset",
                                   f"Delete Preset {slot_num}? This removes all its pinned points."):
            return
        for path in self._preset_files(slot_num):
            full = os.path.join(self.data_dir, path)
            if os.path.exists(full):
                try:
                    os.remove(full)
                    self.log(f"[preset] Deleted {path}")
                except Exception as e:
                    self.log(f"[preset] Could not delete {path}: {e}")
        self._refresh_preset_list()

    def add_deployment_preset(self):
        next_slot = 1
        while next_slot <= 4 and self._preset_exists(next_slot):
            next_slot += 1
        if next_slot > 4:
            messagebox.showwarning("Preset limit reached", "You can only save up to 4 deployment presets.")
            return
        threading.Thread(target=self._run_preset_overlay_sequence, args=(next_slot,), daemon=True).start()

    def _run_preset_overlay_sequence(self, slot_num):
        # One multi-layer overlay: a layer per troop type + heroes + spells, all
        # for this preset (per-type files deploy_preset_N_troop_M.json).
        layers = self._preset_deployment_layers(slot_num)
        proc, sentinel = self._launch_layers_blocking(
            layers, center=True, sentinel_name=f"deploy_preset_{slot_num}_troop_1.json")
        proc.wait()
        if os.path.exists(sentinel):
            self.after(0, lambda: self.log(f"[preset] Preset {slot_num} editing cancelled."))
            return
        self.after(0, lambda: self.log(f"[preset] Preset {slot_num} saved."))
        self.after(0, self._refresh_preset_list)

    def _add_panel(self, parent, title, items):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=14, weight="bold"), text_color=ACCENT).grid(
            row=0, column=0, columnspan=2, padx=0, pady=(0, 8), sticky="w"
        )
        for idx, item in enumerate(items):
            if isinstance(item, tuple) and len(item) == 2:
                key, label = item
                disabled = False
            else:
                key, label, disabled = item
            self._add_config_item(parent, key, label, idx + 1, disabled=disabled)

    def _add_config_item(self, parent, key, label, row, disabled=False):
        ctk.CTkLabel(parent, text=label).grid(row=row, column=0, padx=0, pady=10, sticky="w")
        entry = ctk.CTkEntry(parent, width=220)
        entry.grid(row=row, column=1, padx=(10, 0), pady=10, sticky="ew")
        entry.insert(0, str(self.config.get(key, "")))
        if disabled:
            entry.configure(state="disabled", fg_color="gray20")
        self.entries[key] = entry

    def _build_troops_panel(self, panel):
        """Troops & Heroes panel with a per-troop-type deploy-count input that
        appears once for each troop type (driven by 'Troop Types')."""
        ctk.CTkLabel(panel, text="Troops & Heroes", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=ACCENT).grid(row=0, column=0, columnspan=2, padx=0, pady=(0, 8), sticky="w")

        ctk.CTkLabel(panel, text="Troop Types (bar icons)").grid(row=1, column=0, padx=0, pady=10, sticky="w")
        slots_entry = ctk.CTkEntry(panel, width=220)
        slots_entry.grid(row=1, column=1, padx=(10, 0), pady=10, sticky="ew")
        slots_entry.insert(0, str(self.config.get("num_troop_slots", "")))
        self.entries["num_troop_slots"] = slots_entry
        slots_entry.bind("<KeyRelease>", lambda e: self._rebuild_troop_counts())
        slots_entry.bind("<FocusOut>",  lambda e: self._rebuild_troop_counts())

        # Dynamic per-type "how many to deploy" inputs live in this frame.
        self.troop_counts_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.troop_counts_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.troop_counts_frame.grid_columnconfigure(0, weight=1)
        self.troop_counts_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(panel, text="Number of Heroes").grid(row=3, column=0, padx=0, pady=10, sticky="w")
        heroes_entry = ctk.CTkEntry(panel, width=220)
        heroes_entry.grid(row=3, column=1, padx=(10, 0), pady=10, sticky="ew")
        heroes_entry.insert(0, str(self.config.get("num_heroes", "")))
        self.entries["num_heroes"] = heroes_entry

        self._rebuild_troop_counts()

    def _rebuild_troop_counts(self):
        """Show exactly one 'troops of type N to deploy' input per troop type,
        preserving whatever the user already typed."""
        if not hasattr(self, "troop_counts_frame"):
            return
        # Preserve current typed values, then drop the old dynamic entries.
        cache = {}
        for k in [k for k in self.entries if k.startswith("troops_type_")]:
            w = self.entries[k]
            if isinstance(w, ctk.CTkEntry):
                try:
                    cache[k] = w.get()
                except Exception:
                    pass
            del self.entries[k]
        for w in self.troop_counts_frame.winfo_children():
            w.destroy()

        try:
            n = int(self.entries["num_troop_slots"].get() or 0)
        except (ValueError, TypeError):
            n = 0
        n = max(0, min(n, 10))

        # Collapse the container when there are no per-type inputs, otherwise an
        # empty CTkFrame reserves its default 200px height (a big blank gap).
        if n == 0:
            self.troop_counts_frame.grid_remove()
            return
        self.troop_counts_frame.grid()

        # Migration: an older config has only num_troops_total. If no per-type
        # counts exist yet, seed the fields by splitting that total evenly.
        has_per_type = any(f"troops_type_{i}" in self.config for i in range(1, 11))
        old_total = 0
        if not has_per_type and n > 0:
            try:
                old_total = int(self.config.get("num_troops_total", 0) or 0)
            except (ValueError, TypeError):
                old_total = 0

        for i in range(1, n + 1):
            key = f"troops_type_{i}"
            ctk.CTkLabel(self.troop_counts_frame, text=f"   • Troops of type {i} to deploy").grid(
                row=i - 1, column=0, padx=0, pady=6, sticky="w")
            e = ctk.CTkEntry(self.troop_counts_frame, width=220)
            e.grid(row=i - 1, column=1, padx=(10, 0), pady=6, sticky="ew")
            if key in cache:
                val = cache[key]
            elif key in self.config:
                val = str(self.config.get(key))
            elif old_total:
                val = str(old_total // n + (old_total % n if i == 1 else 0))
            else:
                val = ""
            e.insert(0, val)
            self.entries[key] = e

    def _build_spells_panel(self, panel):
        """Spells & Strategy panel with a per-spell-type deploy-count input that
        appears once for each spell type (driven by 'Spell Types')."""
        ctk.CTkLabel(panel, text="Spells & Strategy", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=ACCENT).grid(row=0, column=0, columnspan=2, padx=0, pady=(0, 8), sticky="w")

        ctk.CTkLabel(panel, text="Spell Types (bar icons)").grid(row=1, column=0, padx=0, pady=10, sticky="w")
        slots_entry = ctk.CTkEntry(panel, width=220)
        slots_entry.grid(row=1, column=1, padx=(10, 0), pady=10, sticky="ew")
        slots_entry.insert(0, str(self.config.get("num_spell_slots", "")))
        self.entries["num_spell_slots"] = slots_entry
        slots_entry.bind("<KeyRelease>", lambda e: self._rebuild_spell_counts())
        slots_entry.bind("<FocusOut>",  lambda e: self._rebuild_spell_counts())

        self.spell_counts_frame = ctk.CTkFrame(panel, fg_color="transparent")
        self.spell_counts_frame.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.spell_counts_frame.grid_columnconfigure(0, weight=1)
        self.spell_counts_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(panel, text="Spell Delay (sec)").grid(row=3, column=0, padx=0, pady=10, sticky="w")
        delay_entry = ctk.CTkEntry(panel, width=220)
        delay_entry.grid(row=3, column=1, padx=(10, 0), pady=10, sticky="ew")
        delay_entry.insert(0, str(self.config.get("spell_delay", self.config.get("rage_delay", ""))))
        self.entries["spell_delay"] = delay_entry

        self._rebuild_spell_counts()

    def _rebuild_spell_counts(self):
        """Show one 'spells of type N to deploy' input per spell type."""
        if not hasattr(self, "spell_counts_frame"):
            return
        cache = {}
        for k in [k for k in self.entries if k.startswith("spells_type_")]:
            w = self.entries[k]
            if isinstance(w, ctk.CTkEntry):
                try:
                    cache[k] = w.get()
                except Exception:
                    pass
            del self.entries[k]
        for w in self.spell_counts_frame.winfo_children():
            w.destroy()

        try:
            n = int(self.entries["num_spell_slots"].get() or 0)
        except (ValueError, TypeError):
            n = 0
        n = max(0, min(n, 10))

        # Collapse the container when empty (avoids a 200px blank gap).
        if n == 0:
            self.spell_counts_frame.grid_remove()
            return
        self.spell_counts_frame.grid()

        # Migration: an older config had a single num_rage. Seed type 1 with it.
        has_per_type = any(f"spells_type_{i}" in self.config for i in range(1, 11))
        old_rage = 0
        if not has_per_type and n > 0:
            try:
                old_rage = int(self.config.get("num_rage", 0) or 0)
            except (ValueError, TypeError):
                old_rage = 0

        for i in range(1, n + 1):
            key = f"spells_type_{i}"
            ctk.CTkLabel(self.spell_counts_frame, text=f"   • Spells of type {i} to deploy").grid(
                row=i - 1, column=0, padx=0, pady=6, sticky="w")
            e = ctk.CTkEntry(self.spell_counts_frame, width=220)
            e.grid(row=i - 1, column=1, padx=(10, 0), pady=6, sticky="ew")
            if key in cache:
                val = cache[key]
            elif key in self.config:
                val = str(self.config.get(key))
            elif old_rage and i == 1:
                val = str(old_rage)
            else:
                val = ""
            e.insert(0, val)
            self.entries[key] = e

    def _update_button_states(self):
        is_configured = self.config.get("setup_complete", False)
        
        # Check if all required fields are filled for the setup button.
        # Use "is not None" instead of truthy checks so a legitimately-entered
        # 0 (e.g. "I have 0 heroes") still counts as filled in, rather than
        # being treated the same as an empty/missing field.
        def _is_filled(key):
            val = self.config.get(key)
            return val is not None and val != ""

        has_troops = (_is_filled("num_troop_slots") and
                      _is_filled("num_heroes"))
        # Spells are optional; only the type count needs to be filled (0 is fine).
        has_spells = _is_filled("num_spell_slots")
        has_loot_target = _is_filled("min_gold")

        setup_ready = has_troops and has_spells and has_loot_target
        
        if is_configured:
            self.start_btn.configure(state="normal", fg_color=ACCENT)
            self.start_btn_glow_frame.configure(fg_color=ACCENT)
            self.edit_deploy_btn.configure(state="normal", fg_color=ACCENT)
            self.edit_troop_slots_btn.configure(state="normal", fg_color=ACCENT)
            self.setup_warning.grid_forget()
            self.setup_complete_label.grid(row=10, column=0, padx=20, pady=(0, 20))
            # Start glow effect
            self.glow_active = True
            self._glow_index = 0
            self._animate_glow()
        else:
            # We keep the start button clickable but it will show a message
            # This fulfills "unclickable" (functionally) and "message if they start"
            # However, for "edit" buttons, we make them strictly disabled as requested
            self.start_btn.configure(state="normal", fg_color="#6c757d")
            self.start_btn_glow_frame.configure(fg_color="#6c757d")
            self.edit_deploy_btn.configure(state="disabled", fg_color="gray30")
            self.edit_troop_slots_btn.configure(state="disabled", fg_color="gray30")
            self.setup_warning.grid(row=10, column=0, padx=20, pady=(0, 20))
            self.setup_complete_label.grid_forget()
            # Stop glow effect
            self.glow_active = False
        
        # Disable "RUN FULL GUIDED SETUP" button if required fields are empty
        if setup_ready:
            self.run_setup_btn.configure(state="normal", fg_color="#6c757d")
        else:
            self.run_setup_btn.configure(state="disabled", fg_color="gray30")

    def select_frame_by_name(self, name):
        # Set button color for selected button
        self.dashboard_button.configure(fg_color=("gray75", "gray25") if name == "dashboard" else "transparent")
        self.config_button.configure(fg_color=("gray75", "gray25") if name == "config" else "transparent")
        self.upgrade_button.configure(fg_color=("gray75", "gray25") if name == "upgrade" else "transparent")
        self.preview_button.configure(fg_color=("gray75", "gray25") if name == "preview" else "transparent")
        self.history_button.configure(fg_color=("gray75", "gray25") if name == "history" else "transparent")
        self.developer_button.configure(fg_color=("gray75", "gray25") if name == "developer" else "transparent")

        # Show selected frame
        if name == "dashboard": self.dashboard_frame.grid(row=0, column=1, sticky="nsew")
        else: self.dashboard_frame.grid_forget()
        if name == "config":
            self.config_frame.grid(row=0, column=1, sticky="nsew")
            self._refresh_readiness()
        else: self.config_frame.grid_forget()
        if name == "upgrade":
            self.upgrade_frame.grid(row=0, column=1, sticky="nsew")
            self._refresh_upgrade_hint()
        else: self.upgrade_frame.grid_forget()
        if name == "preview":
            self.preview_frame.grid(row=0, column=1, sticky="nsew")
            self.refresh_preview_once()
        else: self.preview_frame.grid_forget()
        if name == "history": self.history_frame.grid(row=0, column=1, sticky="nsew")
        else: self.history_frame.grid_forget()
        if name == "developer": self.developer_frame.grid(row=0, column=1, sticky="nsew")
        else: self.developer_frame.grid_forget()

    def load_initial_config(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f: return json.load(f)
            except: pass
        return {}

    def _collect_config(self):
        """Build the config dict from the current entry widgets (without saving),
        including the derived totals. Used by both save and live validation."""
        new_config = {}
        for key, entry in self.entries.items():
            if hasattr(entry, "cget") and entry.cget("state") == "disabled":
                new_config[key] = self.config.get(key, 0)
                continue
            if isinstance(entry, ctk.CTkOptionMenu):
                new_config[key] = entry.get()
                continue
            if isinstance(entry, ctk.CTkSwitch):
                new_config[key] = bool(entry.get())
                continue
            try: new_config[key] = int(entry.get())
            except: new_config[key] = entry.get()

        new_config["setup_complete"] = self.config.get("setup_complete", False)

        def _sum(prefix):
            t = 0
            for k, v in new_config.items():
                if k.startswith(prefix):
                    try: t += int(v)
                    except (ValueError, TypeError): pass
            return t

        new_config["num_troops_total"] = _sum("troops_type_")
        spell_total = _sum("spells_type_")
        new_config["num_rage"] = spell_total
        new_config["has_rage"] = spell_total > 0
        if "spell_delay" in new_config:
            new_config["rage_delay"] = new_config["spell_delay"]
        return new_config

    def save_config(self):
        new_config = self._collect_config()
        with open(self.config_file, "w") as f: json.dump(new_config, f, indent=2)
        self.config = new_config
        self._update_button_states()
        self._refresh_readiness()
        self.log("Configuration saved successfully.")

    # ── Setup readiness / validation ─────────────────────────────
    def _validate_config(self, cfg=None):
        """Return [(level, message)] where level is 'ok' | 'warn' | 'error'.
        Checks the live (unsaved) config plus which point/clip files exist."""
        c = cfg if cfg is not None else self._collect_config()

        def num(k):
            try: return int(c.get(k, 0) or 0)
            except (ValueError, TypeError): return 0
        def has(name):
            return os.path.exists(os.path.join(self.data_dir, name))
        def npts(name, key="points"):
            try: return len(self._load_points(name, key))
            except Exception: return 0

        issues = []
        nt, nh, ns = num("num_troop_slots"), num("num_heroes"), num("num_spell_slots")
        preset_mode = bool(c.get("deploy_preset_enabled", False))

        # Army bar
        if nt < 1:
            issues.append(("error", "Set 'Troop Types' to at least 1."))
        expected_bar = max(nt, 0) + max(nh, 0) + max(ns, 0)
        bar = npts("troop_slots.json", "slots")
        if bar == 0:
            issues.append(("error", "Army bar not pinned — use EDIT PIN TROOP BAR SLOTS."))
        elif expected_bar and bar != expected_bar:
            issues.append(("error", f"Army bar has {bar} icons but config expects {expected_bar} "
                                    f"({nt} troops + {nh} heroes + {ns} spells) — re-pin the bar."))
        else:
            issues.append(("ok", f"Army bar pinned ({bar} icons)."))

        # Deployment
        if preset_mode:
            if not any(self._preset_exists(k) for k in range(1, 5)):
                issues.append(("error", "Preset Mode is ON but no presets created — add one."))
            else:
                issues.append(("ok", "Preset Mode: presets configured."))
        else:
            for i in range(1, nt + 1):
                cnt, p = num(f"troops_type_{i}"), npts(f"deploy_troop_{i}.json")
                if p == 0:
                    issues.append(("warn", f"Troop {i}: no deploy pins — open EDIT DEPLOYMENT."))
                elif cnt and p != cnt:
                    issues.append(("warn", f"Troop {i}: {p} pins but count is {cnt}."))
            if nh > 0 and npts("hero_points.json") == 0:
                issues.append(("warn", "Heroes: no deploy pins (they'll use the first troop point)."))
            for i in range(1, ns + 1):
                cnt, p = num(f"spells_type_{i}"), npts(f"spell_{i}_points.json")
                if p == 0:
                    issues.append(("warn", f"Spell {i}: no deploy pins — open EDIT DEPLOYMENT."))
                elif cnt and p != cnt:
                    issues.append(("warn", f"Spell {i}: {p} pins but count is {cnt}."))

        # Loot / scouting
        if not str(c.get("min_gold", "")).strip():
            issues.append(("warn", "Minimum Gold target not set."))
        if num("min_elixir") > 0 and not has("scout_elixir_roi.json"):
            issues.append(("warn", "Min Elixir is set but no ELIXIR scout box drawn — it will be ignored."))

        # Auto upgrade
        if c.get("auto_upgrade_enabled", False):
            if not (has("gold_full.png") or has("elixir_full.png")):
                issues.append(("error", "Auto-upgrade ON but no GOLD/ELIXIR 'full' reference captured."))
            if c.get("auto_upgrade_walls", True) and (npts("wall_buttons.json") < 3 or npts("wall_targets.json") == 0):
                issues.append(("error", "Upgrade Walls ON but wall buttons/targets not fully pinned."))
            if c.get("auto_upgrade_buildings", True):
                if not has("builders_busy.png"):
                    issues.append(("error", "Upgrade Buildings ON but Builders-Busy reference missing "
                                            "(needed to avoid a gems 'buy builder' popup)."))
                if npts("building_buttons.json") == 0 or npts("building_targets.json") == 0:
                    issues.append(("error", "Upgrade Buildings ON but building buttons/targets not pinned."))

        if not any(lvl != "ok" for lvl, _ in issues):
            issues.append(("ok", "Everything looks ready — you can START BOT."))
        return issues

    def _refresh_upgrade_hint(self):
        """Live 'if you enable this, you must…' hint for the Auto Upgrade tab."""
        if not hasattr(self, "upgrade_hint"):
            return
        self._refresh_readiness()   # keep the config-tab checklist in sync too
        c = self._collect_config()
        def has(n): return os.path.exists(os.path.join(self.data_dir, n))
        def npts(n): return len(self._load_points(n, "points"))
        if not c.get("auto_upgrade_enabled", False):
            self.upgrade_hint.configure(text="Auto-upgrade is OFF — enable it to use the pins below.",
                                        text_color="gray60")
            return
        missing = []
        if not (has("gold_full.png") or has("elixir_full.png")):
            missing.append("capture a GOLD or ELIXIR \"full\" reference")
        if c.get("auto_upgrade_walls", True) and (npts("wall_buttons.json") < 3 or npts("wall_targets.json") == 0):
            missing.append("pin WALL buttons + at least one WALL target")
        if c.get("auto_upgrade_buildings", True):
            if not has("builders_busy.png"):
                missing.append("capture the BUILDERS-BUSY reference (avoids a gems buy-builder popup)")
            if npts("building_buttons.json") == 0 or npts("building_targets.json") == 0:
                missing.append("pin BUILDING buttons + at least one BUILDING target")
        if missing:
            self.upgrade_hint.configure(text="⚠ Still needed:  " + ";  ".join(missing) + ".",
                                        text_color="#FF8C5A")
        else:
            self.upgrade_hint.configure(text="✅ Auto-upgrade is fully set up.", text_color="#39FF14")

    def _refresh_readiness(self):
        """Repopulate the readiness panel from the live config."""
        if not hasattr(self, "readiness_frame"):
            return
        for w in self.readiness_frame.winfo_children():
            w.destroy()
        icon = {"ok": ("✅", "#39FF14"), "warn": ("⚠", "#FFC400"), "error": ("❌", "#FF5A5A")}
        for lvl, msg in self._validate_config():
            sym, col = icon.get(lvl, ("•", "gray70"))
            ctk.CTkLabel(self.readiness_frame, text=f"{sym}  {msg}", anchor="w", justify="left",
                         text_color=col, font=ctk.CTkFont(size=12), wraplength=760).pack(
                anchor="w", fill="x", pady=1)

    def reset_all_config(self):
        if self.bot_process:
            messagebox.showwarning("Bot Running", "Stop the bot before resetting configuration.")
            return

        confirmed = messagebox.askyesno(
            "Reset All Config",
            "This will permanently delete config.json and all .json point files "
            "(troop slots, deploy points, rage points, and all presets).\n\n"
            "The bot will need a full guided setup before it can run again.\n\n"
            "Are you sure?",
        )
        if not confirmed:
            return

        core_files = [
            "config.json",
            "troop_slots.json",
            "deploy_points.json",
            "rage_points.json",
            "hero_points.json",
        ]
        preset_files = [
            f for f in os.listdir(self.data_dir)
            if re.match(r"^(deploy_preset_\d+_troop_\d+|spell_preset_\d+_type_\d+|(deploy|rage|hero)_preset_\d+|deploy_troop_\d+|spell_\d+_points)\.json$", f)
        ]

        deleted = []
        for fname in core_files + preset_files:
            full = os.path.join(self.data_dir, fname)
            if os.path.exists(full):
                try:
                    os.remove(full)
                    deleted.append(fname)
                except Exception as e:
                    self.log(f"[reset] Could not delete {fname}: {e}")

        # Reset in-memory state
        self.config = {}
        self.active_preset_id = None

        # Clear config entry widgets
        for key, widget in self.entries.items():
            if isinstance(widget, ctk.CTkEntry) and widget.cget("state") != "disabled":
                widget.delete(0, "end")

        self._update_button_states()
        self._refresh_preset_list()

        if deleted:
            self.log(f"[reset] Deleted: {', '.join(deleted)}")
        self.log("[reset] All configuration cleared. Run FULL GUIDED SETUP to reconfigure.")

    def refresh_ui(self):
        """Refresh and reload all UI elements and configuration."""
        self.config = self.load_initial_config()
        
        # Update all entry fields with current config values
        for key, widget in self.entries.items():
            if isinstance(widget, ctk.CTkEntry) and widget.cget("state") != "disabled":
                widget.delete(0, "end")
                widget.insert(0, str(self.config.get(key, "")))
            elif isinstance(widget, ctk.CTkOptionMenu):
                widget.set(self.config.get(key, "sequence"))
            elif isinstance(widget, ctk.CTkSwitch):
                widget.set(bool(self.config.get(key, False)))

        # Rebuild the per-type deploy-count inputs from the reloaded config.
        self._rebuild_troop_counts()
        self._rebuild_spell_counts()

        # Refresh preset list
        self._refresh_preset_list()
        
        # Update button states
        self._update_button_states()
        
        # Update preset legend in preview
        self._update_preset_legend()

        # Refresh readiness checklist + auto-upgrade hint
        self._refresh_readiness()
        self._refresh_upgrade_hint()

        self.log("UI refreshed successfully.")

    def _animate_glow(self):
        """Animate a glowing border effect around the START BOT button."""
        if not self.glow_active:
            return
        
        # Warm glow pulse cycling around the brand accent color (no neon green)
        glow_colors = [
            "#df7d59",  # Base accent
            "#e2895f",
            "#e69566",
            "#e9a16c",
            "#ecad73",
            "#f0b97a",  # Brightest point
            "#ecad73",
            "#e9a16c",
            "#e69566",
            "#e2895f",
            "#df7d59",  # Back to base
        ]
        
        # Cycle through the glow colors
        if not hasattr(self, '_glow_index'):
            self._glow_index = 0
        
        color = glow_colors[self._glow_index % len(glow_colors)]
        self.start_btn_glow_frame.configure(fg_color=color)
        
        self._glow_index += 1
        
        # Schedule next animation frame (update every 80ms for smooth effect)
        self.after(80, self._animate_glow)

    def open_url(self, url):
        webbrowser.open_new_tab(url)

    def log(self, message):
        self.log_textbox.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_textbox.see("end")

    def _get_entry_value(self, key, fallback=0):
        entry = self.entries.get(key)
        if entry is None:
            return fallback
        try:
            raw = entry.get().strip()
            if raw == "":
                return fallback
            return int(raw)
        except ValueError:
            return raw

    def _build_setup_answers(self):
        num_troop_slots = self._get_entry_value("num_troop_slots", 1)
        # Total troops = sum of the per-type deploy counts.
        num_troops_total = 0
        for i in range(1, 11):
            v = self._get_entry_value(f"troops_type_{i}", 0)
            try:
                num_troops_total += int(v)
            except (ValueError, TypeError):
                pass
        if num_troops_total == 0:
            num_troops_total = self._get_entry_value("num_troops_total", 10)
        num_heroes = self._get_entry_value("num_heroes", 0)
        num_spells = self._get_entry_value("num_spells", 0)
        spells_per_ad = self._get_entry_value("spells_per_ad", 0)
        num_rage = self._get_entry_value("num_rage", 0)
        rage_delay = self._get_entry_value("rage_delay", 10)
        min_gold = self._get_entry_value("min_gold", 200000)

        answers = [
            str(num_troop_slots),
            str(num_troops_total),
            str(num_heroes),
            str(num_spells),
        ]

        if num_spells > 0:
            answers.append(str(spells_per_ad))

        has_rage = num_rage > 0
        answers.append("y" if has_rage else "n")

        if has_rage:
            answers.extend([str(num_rage), str(rage_delay)])

        answers.append(str(min_gold))
        return answers

    def toggle_bot(self):
        if self.bot_process:
            self.stop_bot()
            return

        if not self.config.get("setup_complete", False):
            messagebox.showwarning("Setup Required", "Configuration is empty! Please run the full guided setup before starting the bot.")
            self.log("Configuration -> Advanced -> RUN FULL GUIDED SETUP")
            return

        # Block on hard errors (missing pins / references); warnings are allowed.
        errors = [msg for lvl, msg in self._validate_config(self.config) if lvl == "error"]
        if errors:
            self._refresh_readiness()
            messagebox.showerror("Not ready to start",
                                 "Fix these before starting the bot:\n\n• " + "\n• ".join(errors))
            self.log("[readiness] Blocked start — " + str(len(errors)) + " issue(s). See ⚡ Setup Readiness.")
            return

        self.start_bot()

    def start_bot(self):
        self.start_btn.configure(text="STOP BOT", fg_color="#dc3545", hover_color="#c82333")
        self.status_label.configure(text="● SYSTEM ACTIVE", text_color=ACCENT)
        self.stop_event.clear()
        self.start_time = time.time()
        self.active_preset_id = None
        threading.Thread(target=self.bot_loop, daemon=True).start()
        threading.Thread(target=self.update_runtime, daemon=True).start()
        threading.Thread(target=self.refresh_preview, daemon=True).start()

    def stop_bot(self):
        self.stop_event.set()
        if self.bot_process: self.bot_process.terminate()
        self.bot_process = None
        self.start_btn.configure(text="START BOT", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.status_label.configure(text="● SYSTEM READY", text_color="#FFD700")

    def update_runtime(self):
        while not self.stop_event.is_set():
            elapsed = int(time.time() - self.start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            self.after(0, lambda: self.card_runtime.configure(text=f"{h:02d}:{m:02d}:{s:02d}"))
            time.sleep(1)

    # Color mapping for each point category — kept in sync with the
    # legend swatches in _create_main_content (BGR-friendly hex -> RGB tuple).
    OVERLAY_STYLES = {
        "slots":  {"file": "troop_slots.json",  "key": "slots",  "color": (0, 191, 255), "label": "T"},
        "deploy": {"file": "deploy_points.json", "key": "points", "color": (57, 255, 20),  "label": "D"},
        "rage":   {"file": "rage_points.json",   "key": "points", "color": (255, 59, 59),  "label": "R"},
    }
    HERO_RGB = (63, 167, 255)
    # Per-type marker colors (RGB), mirroring the deployment editor palettes.
    TROOP_RGB = [(57, 255, 20), (255, 212, 0), (0, 229, 255), (255, 140, 0), (185, 128, 255),
                 (0, 255, 156), (255, 93, 162), (255, 255, 255), (124, 252, 0), (255, 127, 80)]
    SPELL_RGB = [(255, 59, 59), (255, 105, 180), (255, 20, 147), (220, 20, 60), (255, 127, 80),
                 (255, 182, 193), (199, 21, 133), (250, 128, 114), (233, 150, 122), (255, 99, 71)]

    # Distinct colors per preset slot so overlapping presets stay readable.
    # Deploy points use the bright variant, rage points use the dim variant,
    # so within one preset you can still tell deploy vs rage apart.
    PRESET_COLORS = {
        1: {"deploy": (57, 255, 20),  "rage": (255, 59, 59)},
        2: {"deploy": (255, 191, 0),  "rage": (255, 0, 200)},
        3: {"deploy": (0, 255, 255),  "rage": (180, 80, 255)},
        4: {"deploy": (255, 140, 0),  "rage": (120, 255, 120)},
    }

    def _load_points(self, filename, key):
        """Read a {"<key>": [{"x":.., "y":..}, ...]} JSON file from data/. Returns [] on failure."""
        path = filename if os.path.isabs(filename) else os.path.join(self.data_dir, filename)
        if not os.path.exists(path):
            return []
        try:
            with open(path) as f:
                data = json.load(f)
            return [(p["x"], p["y"]) for p in data.get(key, [])]
        except Exception:
            return []

    def _draw_marker(self, draw, x, y, color, label, radius):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                     outline=color, width=max(2, radius // 3))
        draw.line([x - radius * 1.6, y, x + radius * 1.6, y], fill=color, width=1)
        draw.line([x, y - radius * 1.6, x, y + radius * 1.6], fill=color, width=1)
        draw.text((x + radius + 3, y - radius - 2), label, fill=color)

    def _draw_overlays(self, img):
        """Draw troop slot / deploy / rage points onto a PIL image (native resolution),
        scaling marker sizes to the image so it still looks right after resizing.

        Mirrors app.py's deployment logic: if Preset Mode is enabled, draws every
        configured deploy_preset_N.json + its paired rage_preset_N.json (numbered
        and color-coded per preset). If Preset Mode is disabled, draws the plain
        deploy_points.json + rage_points.json — whichever the bot will actually
        use for the next raid.
        """
        draw = ImageDraw.Draw(img)
        w, h = img.size
        radius = max(4, int(min(w, h) * 0.012))

        # Troop slots are always drawn from the same file regardless of preset mode.
        if self.overlay_vars["slots"].get():
            style = self.OVERLAY_STYLES["slots"]
            for (x, y) in self._load_points(style["file"], style["key"]):
                self._draw_marker(draw, x, y, style["color"], style["label"], radius)

        preset_enabled = bool(self.config.get("deploy_preset_enabled", False))

        if not preset_enabled:
            if self.overlay_vars["deploy"].get():
                # One layer per troop type (deploy_troop_N.json), colour-coded.
                n = max(1, min(self._cfg_num("num_troop_slots") or 1, 10))
                drew_any = False
                for i in range(1, n + 1):
                    color = self.TROOP_RGB[(i - 1) % len(self.TROOP_RGB)]
                    for (x, y) in self._load_points(f"deploy_troop_{i}.json", "points"):
                        self._draw_marker(draw, x, y, color, f"T{i}", radius)
                        drew_any = True
                if not drew_any:  # legacy single-file fallback
                    for (x, y) in self._load_points("deploy_points.json", "points"):
                        self._draw_marker(draw, x, y, (57, 255, 20), "D", radius)
            if self.overlay_vars["hero"].get():
                for (x, y) in self._load_points("hero_points.json", "points"):
                    self._draw_marker(draw, x, y, self.HERO_RGB, "H", radius)
            if self.overlay_vars["rage"].get():
                # One layer per spell type (spell_N_points.json), colour-coded.
                m = max(0, min(self._cfg_num("num_spell_slots"), 10))
                drew_any = False
                for i in range(1, m + 1):
                    color = self.SPELL_RGB[(i - 1) % len(self.SPELL_RGB)]
                    for (x, y) in self._load_points(f"spell_{i}_points.json", "points"):
                        self._draw_marker(draw, x, y, color, f"S{i}", radius)
                        drew_any = True
                if not drew_any:  # legacy single-file fallback
                    for (x, y) in self._load_points("rage_points.json", "points"):
                        self._draw_marker(draw, x, y, (255, 59, 59), "R", radius)
            return img

        # Preset mode on. If the bot has told us which preset it's actually
        # running (via the "[deploy] Using preset presetN for raid" log line),
        # show only that one — otherwise (bot not started yet) fall back to
        # showing every configured preset so pins can still be verified.
        if self.active_preset_id:
            preset_ids = [self.active_preset_id]
        else:
            raw_order = self.config.get("deploy_preset_order", "preset1,preset2,preset3")
            preset_ids = [p.strip() for p in str(raw_order).split(",") if p.strip()] or ["preset1"]

        for preset_id in preset_ids:
            try:
                slot_num = int(preset_id.replace("preset", ""))
            except ValueError:
                continue
            colors = self.PRESET_COLORS.get(slot_num, self.PRESET_COLORS[1])

            if self.overlay_vars["deploy"].get():
                # Per-troop-type points for this preset (T1, T2, …).
                n = max(1, min(self._cfg_num("num_troop_slots") or 1, 10))
                drew_any = False
                for i in range(1, n + 1):
                    color = self.TROOP_RGB[(i - 1) % len(self.TROOP_RGB)]
                    for (x, y) in self._load_points(f"deploy_preset_{slot_num}_troop_{i}.json", "points"):
                        self._draw_marker(draw, x, y, color, f"P{slot_num}T{i}", radius)
                        drew_any = True
                if not drew_any:  # legacy shared file / preset1 fallback
                    pts = self._load_points(f"deploy_preset_{slot_num}.json", "points")
                    if not pts and slot_num == 1:
                        pts = self._load_points("deploy_points.json", "points")
                    for (x, y) in pts:
                        self._draw_marker(draw, x, y, colors["deploy"], f"D{slot_num}", radius)

            if self.overlay_vars["hero"].get():
                pts = self._load_points(f"hero_preset_{slot_num}.json", "points")
                if not pts and slot_num == 1:
                    pts = self._load_points("hero_points.json", "points")
                for (x, y) in pts:
                    self._draw_marker(draw, x, y, self.HERO_RGB, f"H{slot_num}", radius)

            if self.overlay_vars["rage"].get():
                # Per-spell-type points for this preset (S1, S2, …).
                m = max(0, min(self._cfg_num("num_spell_slots"), 10))
                drew_any = False
                for i in range(1, m + 1):
                    color = self.SPELL_RGB[(i - 1) % len(self.SPELL_RGB)]
                    for (x, y) in self._load_points(f"spell_preset_{slot_num}_type_{i}.json", "points"):
                        self._draw_marker(draw, x, y, color, f"P{slot_num}S{i}", radius)
                        drew_any = True
                if not drew_any:  # legacy shared file / preset1 fallback
                    pts = self._load_points(f"rage_preset_{slot_num}.json", "points")
                    if not pts and slot_num == 1:
                        pts = self._load_points("rage_points.json", "points")
                    for (x, y) in pts:
                        self._draw_marker(draw, x, y, colors["rage"], f"R{slot_num}", radius)

        return img

    def refresh_preview_once(self):
        """Grab a fresh ADB screenshot and render it immediately."""
        self.after(0, self._update_preset_legend)
        self.after(0, lambda: self.screen_canvas.configure(text="📡  Grabbing screenshot...", image=""))
        if hasattr(self, "refresh_screenshot_btn"):
            self.after(0, lambda: self.refresh_screenshot_btn.configure(state="disabled", text="📡  Connecting..."))
        def _run():
            self._render_preview_frame()
            if hasattr(self, "refresh_screenshot_btn"):
                self.after(0, lambda: self.refresh_screenshot_btn.configure(state="normal", text="📷  Refresh Screenshot"))
        threading.Thread(target=_run, daemon=True).start()

    def _grab_adb_screenshot(self):
        """Take a live screenshot from LDPlayer via ADB. Returns a PIL Image or None."""
        try:
            result = subprocess.run(
                [ADB, "-s", "127.0.0.1:5555", "exec-out", "screencap", "-p"],
                capture_output=True, timeout=12, creationflags=_NO_WINDOW
            )
            if result.stdout and len(result.stdout) > 1000:
                return Image.open(io.BytesIO(result.stdout)).convert("RGB")
        except Exception as e:
            print(f"[preview] ADB screenshot failed: {e}")
        return None

    def _render_preview_frame(self):
        img = None

        # 1. Try a live ADB screenshot first
        img = self._grab_adb_screenshot()

        # 2. Fall back to the debug file saved by app.py (correct absolute path)
        if img is None:
            debug_path = os.path.join(self.data_dir, "debug_battle_screen.png")
            if os.path.exists(debug_path):
                try:
                    img = Image.open(debug_path).convert("RGB")
                except Exception:
                    pass

        if img is None:
            self.after(0, lambda: self.screen_canvas.configure(
                text="⚠  Could not connect to LDPlayer.\nMake sure ADB is enabled on port 5555.",
                image=""
            ))
            return

        try:
            img = self._draw_overlays(img)
            img = img.resize((720, 405), Image.LANCZOS)
            tk_img = ImageTk.PhotoImage(img)
            self.after(0, lambda i=tk_img: self.screen_canvas.configure(image=i, text=""))
        except Exception as e:
            print(f"[preview] Render error: {e}")

    def refresh_preview(self):
        while not self.stop_event.is_set():
            self._render_preview_frame()
            time.sleep(5)

    def bot_loop(self):
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        self.bot_process = subprocess.Popen(
            _bot_cmd("--farm-only"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_NO_WINDOW
        )

        for line in self.bot_process.stdout:
            if self.stop_event.is_set(): break
            clean_line = ansi_escape.sub('', line).strip()
            if not clean_line: continue
            self.after(0, lambda l=clean_line: self.log(l))

            # Parse metrics — "RAID 12" marks the start of each attempt,
            # so this counts every raid attempt (including ones that get
            # aborted early), unlike hooking a single mid-sequence step.
            raid_match = re.match(r"RAID\s+(\d+)", clean_line)
            if raid_match:
                self.attack_count = int(raid_match.group(1))
                self.after(0, lambda c=self.attack_count: self.card_attacks.configure(text=str(c)))
                self.after(0, lambda c=self.attack_count: self.history_list.insert("0.0", f"[{time.strftime('%H:%M')}] Raid #{c} started.\n"))

            # Track which deployment preset the bot is actually using right now,
            # so the Live Preview overlay shows that preset only — not all of them.
            preset_match = re.search(r"\[deploy\]\s+Using preset\s+(preset\d+)\s+for raid", clean_line)
            if preset_match:
                new_preset_id = preset_match.group(1)
                if new_preset_id != self.active_preset_id:
                    self.active_preset_id = new_preset_id
                    self.after(0, self.refresh_preview_once)

        self.after(0, self.stop_bot)

    def run_setup(self):
        self.log("Launching guided setup...")
        threading.Thread(target=self.run_setup_process, daemon=True).start()

    def run_setup_process(self):
        answers = self._build_setup_answers()
        self.log(f"Submitting setup answers: {', '.join(answers)}")

        process = subprocess.Popen(
            _bot_cmd("--setup-only"),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace', bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=_NO_WINDOW
        )

        if process.stdin:
            process.stdin.write("\n".join(answers) + "\n")
            process.stdin.flush()
            process.stdin.close()

        for line in process.stdout:
            self.after(0, lambda l=line.strip(): self.log(f"[SETUP] {l}"))

        process.wait()

        # Reload config after setup
        self.config = self.load_initial_config()
        self.after(0, self._update_button_states)
        if self.config.get("setup_complete", False):
            self.log("Setup complete! You can now start the bot.")
        else:
            self.log("Setup finished but config was not marked complete. Please verify the bot output.")

    def launch_manual_overlay(self, file_path, title, center=False):
        env = os.environ.copy()
        env["OVERLAY_OUTPUT"] = os.path.join(self.data_dir, file_path)
        env["OVERLAY_TITLE"] = title
        if center:
            env["OVERLAY_CENTER"] = "1"
        subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

    def launch_clip_overlay(self, png_name, title):
        """Open the overlay in rectangle-clip mode to capture a 'resource full'
        reference image (drag a box → saved as png_name next to the exe)."""
        env = os.environ.copy()
        env["OVERLAY_CLIP_OUTPUT"] = os.path.join(self.data_dir, png_name)
        env["OVERLAY_TITLE"] = title
        subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

    def launch_rect_overlay(self, json_name, title):
        """Open the overlay in rectangle-coordinate mode to define an OCR box
        (drag a box → its device coords saved as json_name in data/)."""
        env = os.environ.copy()
        env["OVERLAY_RECT_OUTPUT"] = os.path.join(self.data_dir, json_name)
        env["OVERLAY_TITLE"] = title
        subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

    def _cfg_num(self, key):
        try:
            return int(self.config.get(key, 0) or 0)
        except (ValueError, TypeError):
            return 0

    # Layers = list of (key, label, filename, color, count). One overlay edits all.
    _TROOP_COLORS = ["#39FF14", "#FFD400", "#00E5FF", "#FF8C00",
                     "#B980FF", "#00FF9C", "#FF5DA2", "#FFFFFF", "#7CFC00", "#FF7F50"]
    _SPELL_COLORS = ["#FF3B3B", "#FF69B4", "#FF1493", "#DC143C", "#FF7F50",
                     "#FFB6C1", "#C71585", "#FA8072", "#E9967A", "#FF6347"]

    def _main_deployment_layers(self):
        """Main deployment: a layer per troop type + Heroes + a layer per spell type."""
        n = max(1, min(self._cfg_num("num_troop_slots") or 1, 10))
        layers = []
        for i in range(1, n + 1):
            layers.append((f"troop{i}", f"Troop {i}", f"deploy_troop_{i}.json",
                           self._TROOP_COLORS[(i - 1) % len(self._TROOP_COLORS)],
                           self._cfg_num(f"troops_type_{i}")))
        layers.append(("heroes", "Heroes", "hero_points.json", "#3fa7ff", self._cfg_num("num_heroes")))
        for i in range(1, max(0, min(self._cfg_num("num_spell_slots"), 10)) + 1):
            layers.append((f"spell{i}", f"Spell {i}", f"spell_{i}_points.json",
                           self._SPELL_COLORS[(i - 1) % len(self._SPELL_COLORS)],
                           self._cfg_num(f"spells_type_{i}")))
        return layers

    def _preset_deployment_layers(self, slot_num):
        """Same as the main deployment, but writing to this preset's files."""
        n = max(1, min(self._cfg_num("num_troop_slots") or 1, 10))
        layers = []
        for i in range(1, n + 1):
            layers.append((f"troop{i}", f"Troop {i}", f"deploy_preset_{slot_num}_troop_{i}.json",
                           self._TROOP_COLORS[(i - 1) % len(self._TROOP_COLORS)],
                           self._cfg_num(f"troops_type_{i}")))
        layers.append(("heroes", "Heroes", f"hero_preset_{slot_num}.json", "#3fa7ff", self._cfg_num("num_heroes")))
        for i in range(1, max(0, min(self._cfg_num("num_spell_slots"), 10)) + 1):
            layers.append((f"spell{i}", f"Spell {i}", f"spell_preset_{slot_num}_type_{i}.json",
                           self._SPELL_COLORS[(i - 1) % len(self._SPELL_COLORS)],
                           self._cfg_num(f"spells_type_{i}")))
        return layers

    def _layers_env(self, layers, center):
        env = os.environ.copy()
        specs = [{"key": k, "label": lbl, "color": col, "count": cnt,
                  "file": os.path.join(self.data_dir, fname)} for (k, lbl, fname, col, cnt) in layers]
        env["OVERLAY_LAYERS"] = json.dumps(specs)
        env["OVERLAY_TITLE"] = "Edit deployment — Troops / Heroes / Spells"
        if center:
            env["OVERLAY_CENTER"] = "1"
        return env

    def edit_global_deployment(self):
        """Open the multi-layer editor with one layer per troop type + heroes + spells."""
        env = self._layers_env(self._main_deployment_layers(), center=True)
        subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

    def edit_troop_bar_slots(self):
        """Multi-layer editor for the army-bar icons: Troops / Heroes / Spells.
        All layers combine into the single ordered troop_slots.json the bot reads.
        The bar is fixed bottom UI, so no camera centering."""
        layers = [
            ("troops", "Troops", "troop_slots.json", "#39FF14", self._cfg_num("num_troop_slots")),
            ("heroes", "Heroes", "troop_slots.json", "#3fa7ff", self._cfg_num("num_heroes")),
        ]
        # One bar icon per spell type (order: troops → heroes → spells).
        for i in range(1, max(0, min(self._cfg_num("num_spell_slots"), 10)) + 1):
            layers.append((f"spell{i}", f"Spell {i}", "troop_slots.json",
                           self._SPELL_COLORS[(i - 1) % len(self._SPELL_COLORS)], 1))
        env = self._layers_env(layers, center=False)
        env["OVERLAY_TITLE"] = "Pin ARMY BAR icons — Troops / Heroes / Spells"
        env["OVERLAY_COMBINE_FILE"] = os.path.join(self.data_dir, "troop_slots.json")
        subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

    def _launch_layers_blocking(self, layers, center, sentinel_name):
        """Multi-layer overlay that a background thread can .wait() on; returns
        (proc, sentinel_path). Sentinel is removed by the overlay on save."""
        env = self._layers_env(layers, center)
        sentinel_path = os.path.join(self.data_dir, sentinel_name + ".editing")
        try:
            with open(sentinel_path, "w") as f:
                f.write("editing")
        except OSError:
            pass
        env["OVERLAY_SENTINEL"] = sentinel_path
        proc = subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)
        return proc, sentinel_path

    def _launch_overlay_blocking(self, file_path, title, center=False):
        """Like launch_manual_overlay, but returns the Popen handle so a background
        thread can .wait() on it before chaining the next overlay. Never call this
        from the Tk main thread.

        Uses a temp sentinel file to detect if user cancelled:
        - If sentinel exists after overlay exits → user cancelled (don't treat as delete)
        - If sentinel removed → user saved (overlay wrote to file_path)
        """
        env = os.environ.copy()
        env["OVERLAY_OUTPUT"] = os.path.join(self.data_dir, file_path)
        env["OVERLAY_TITLE"] = title
        if center:
            env["OVERLAY_CENTER"] = "1"

        # Create a sentinel file to detect cancel (overlay removes it on successful save)
        sentinel_path = os.path.join(self.data_dir, file_path + ".editing")
        try:
            with open(sentinel_path, 'w') as f:
                f.write("editing")
        except OSError:
            pass
        
        env["OVERLAY_SENTINEL"] = sentinel_path
        return subprocess.Popen(_overlay_cmd(), env=env, creationflags=_NO_WINDOW)

if __name__ == "__main__":
    # Self-dispatch: when the frozen exe relaunches itself for a subprocess, run
    # the bot loop or the overlay IN THIS PROCESS instead of the GUI — so the one
    # exe needs no external Python. (In dev these still run as separate .py files.)
    if "--run-bot" in sys.argv:
        i = sys.argv.index("--run-bot")
        sys.argv = [sys.argv[0]] + sys.argv[i + 1:]   # pass through --farm-only / --setup-only
        import app as _botmod
        _botmod.main()
    elif "--run-overlay" in sys.argv:
        import deploy_overlay as _ovmod
        _ovmod.run()
    else:
        app = ProfessionalCoCBot()
        app.mainloop()