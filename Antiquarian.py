import io
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import filedialog
import tkinter.messagebox as messagebox
import webbrowser
from typing import Union, Dict, Callable, List, Optional, Tuple

import customtkinter as ctk
import yaml
from dotenv import dotenv_values
from Commissioner.record_registry import load_pmt_front_matter, prompt_search_dirs
from Commissioner.winio import replace_with_retry

BASE_DIR = Path(__file__).resolve().parent
APP_VERSION = "0.07.00"

# Path(__file__).resolve().parent lands inside PyInstaller's _internal bundle dir when
# frozen, not the exe's own folder - anything that needs "where the app itself actually
# lives" (e.g. detecting a portable install via a marker file placed next to the exe)
# must resolve via sys.executable instead.
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BASE_DIR

# Both portable delivery paths - the installer's own Portable Installation choice, and the
# raw Antiquarian_Portable_*.zip (see build.py) - carry a ".portable" marker next to the
# exe. Assume no separate Genealogy folder exists yet (e.g. running from a USB drive) and
# default Genealogy Directory to wherever the app itself is, instead of an empty string
# that would otherwise break every downstream path join.
_DEFAULT_GENEALOGY_DIR = str(APP_DIR) if (APP_DIR / ".portable").exists() else ""

# Each tool's own script lives in a fixed subfolder of this codebase - hardcoded rather than
# a configurable Global Settings field, since the folder layout is the codebase's own, not
# something that varies per user or per project the way PROGRAM_DIR does.
SCRIPT_PATHS = {
    "ANALYSIS_SCRIPT": "Paleographer/Paleographer.py",
    "ARCHIVIST_SCRIPT": "Archivist/Archivist.py",
    "VOYAGEUR_SCRIPT": "Voyageur/Voyageur.py",
    "REGISTRAR_SCRIPT": "Registrar/Registrar.py",
    "GAZETTEER_SCRIPT": "Gazetteer/Gazetteer.py",
    "PDFIX_SCRIPT": "PDFix/PDFix.py",
    "CLEANUP_CACHE_SCRIPT": "Paleographer/CacheCleanup.py",
    "AGY_TEST_SCRIPT": "AntiquarianMCP/test_agy_connection.py",
}


def _args_paleographer_api(string_vars: Dict[str, "ctk.StringVar"], debug_file_var: "ctk.StringVar") -> List[str]:
    debug_file = debug_file_var.get().strip()
    return [debug_file] if debug_file else []


def _args_analysis_enrich(string_vars: Dict[str, "ctk.StringVar"], debug_file_var: "ctk.StringVar") -> List[str]:
    args = ["enrich", "--delay", "0.4"]
    limit_val = string_vars.get("SCRIP_ENRICH_LIMIT", ctk.StringVar(value="")).get().strip()
    if limit_val:
        args.extend(["--limit", limit_val])
    return args


def _args_analysis_partition(string_vars: Dict[str, "ctk.StringVar"], debug_file_var: "ctk.StringVar") -> List[str]:
    args = ["partition"]
    out_dir_val = string_vars.get("SCRIP_PARTITION_OUTPUT_DIR", ctk.StringVar(value="")).get().strip()
    if out_dir_val:
        args.extend(["--output-dir", out_dir_val])
    return args


def _args_bare_mode(mode: str) -> Callable[[Dict[str, "ctk.StringVar"], "ctk.StringVar"], List[str]]:
    def _inner(string_vars: Dict[str, "ctk.StringVar"], debug_file_var: "ctk.StringVar") -> List[str]:
        return [mode]
    return _inner


def _args_voyageur_lac(string_vars: Dict[str, "ctk.StringVar"], debug_file_var: "ctk.StringVar") -> List[str]:
    args = ["LAC"]
    gather_url = string_vars.get("GATHER_URL", ctk.StringVar(value="")).get().strip()
    if "heritage.canadiana.ca" in gather_url:
        args.extend(["reel", "--url", gather_url])
    elif gather_url:
        args.extend(["volume", "--volume", gather_url])
    return args


# ARCH-2: declarative CLI-arg assembly. (script_key, mode) -> callable(string_vars,
# debug_file_var) -> extra argv beyond the script path itself. A combination absent from
# this table (e.g. every *_SCRIPT with mode "standalone") launches with no extra args,
# matching the old if/elif chain's implicit fallthrough.
ARG_SPECS: Dict[Tuple[str, str], Callable[[Dict[str, "ctk.StringVar"], "ctk.StringVar"], List[str]]] = {
    ("ANALYSIS_SCRIPT", "paleographer_api"): _args_paleographer_api,
    ("ANALYSIS_SCRIPT", "enrich"): _args_analysis_enrich,
    ("ANALYSIS_SCRIPT", "partition"): _args_analysis_partition,
    ("ANALYSIS_SCRIPT", "resolve-names"): _args_bare_mode("resolve-names"),
    ("ANALYSIS_SCRIPT", "crosscheck"): _args_bare_mode("crosscheck"),
    ("VOYAGEUR_SCRIPT", "A"): _args_bare_mode("A"),
    ("VOYAGEUR_SCRIPT", "FS"): _args_bare_mode("FS"),
    ("VOYAGEUR_SCRIPT", "HBCA"): _args_bare_mode("HBCA"),
    ("VOYAGEUR_SCRIPT", "LAC"): _args_voyageur_lac,
}


def get_config_dir() -> Path:
    if (APP_DIR / ".portable").exists():
        return APP_DIR
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "Antiquarian"
    return Path.home() / "AppData" / "Local" / "Antiquarian"


def env_path_for(subfolder: Optional[str]) -> Path:
    """Resolves the .env config path, isolating module-specific settings into subfolders."""
    config_dir = get_config_dir()
    return config_dir / subfolder / ".env" if subfolder else config_dir / ".env"


def batch_set_env(env_path: Path, updates: Dict[str, str]) -> None:
    """Saves a batch of key-value pairs to a .env file in a single write operation,
    preserving comments and existing keys without triggering Windows file-lock errors."""
    env_path = Path(env_path)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    if env_path.exists():
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []

    updated_keys = set()
    new_lines: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" in line:
            key, _ = line.split("=", 1)
            key = key.strip()
            if key in updates:
                val = updates[key]
                new_lines.append(f"{key}='{val}'")
                updated_keys.add(key)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}='{val}'")

    content = "\n".join(new_lines) + "\n"
    tmp_path = env_path.with_suffix(".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    replace_with_retry(tmp_path, env_path)


# ==========================================
# COLOR PALETTE
# ==========================================
# Dark-mode values from Commissioner/assets/theme.json's own token set (the app forces
# ctk.set_appearance_mode("Dark"), so only these matter visually) - for the handful of
# widgets below that pass their own explicit color kwargs instead of riding the CTk theme.
C_SURFACE = "#202329"
C_SURFACE_RAISED = "#262A31"
C_TEXT = "#E7E6DE"
C_TEXT_MUTED = "#8A8D83"
C_ACCENT = "#8FA0CE"
C_ACCENT_STRONG = "#6C7EAE"
C_ON_ACCENT = "#141A28"
C_BRASS = "#CBA35F"
C_BRASS_SOFT = "#3A2F1C"
C_SUCCESS = "#74C285"
C_SUCCESS_HOVER = "#5CAA6E"
C_DANGER = "#E58585"
C_DANGER_HOVER = "#C96A6A"
C_BORDER = "#33363D"

# ==========================================
# UNIFIED ENV SCHEMA & CONTEXT OVERRIDES
# ==========================================
GLOBAL_VARS = {"API & Processing": {"AGY_MODEL_NAME": "gemini-3.1-pro-high",
                                    "EXTRACTION_ENGINE": "agy",
                                    "AI_API_KEY": "",
                                    "API_BUDGET": "5.00",
                                    "MODEL_NAME": "",
                                    "COST_PER_1M_INPUT": "0.075",
                                    "COST_PER_1M_OUTPUT": "0.30",
                                    "CACHE_DISCOUNT_MULTIPLIER": "0.10"},
               "Global Directories": {"GENEALOGY_DIR": _DEFAULT_GENEALOGY_DIR,
                                      "RM_DIR": "",
                                      "FTM_DIR": "",
                                      "MEDIA_DIR": "Media",
                                      "JSON_DIR": "JSON",
                                      "GEDCOM_OUTPUT_PATH": "GEDCOM",
                                      "PROMPTS_DIR": "Prompts"},
               # Everything identifying who's doing the research and how it's attributed,
               # in one card - was two ("Metadata & Organization", "Standard Links").
               "Researcher & Organization": {"RESEARCHER": "Your Name", "ORG_NAME": "Your Historical Society",
                                             "ROOT_SOURCE_ID": "@S1@",
                                             "SUBM_ADDRESS": "https://www.example.com/contact",
                                             "MGS_GROUP_URL": "https://www.example.com/groups/main",
                                             "ANCESTRY_GROUP_URL": "https://www.ancestry.com/groups/example"}}

# ==========================================
# PATH & FILE PICKER FIELDS CONSTANTS
# ==========================================
GENEALOGY_DIR_SENTINEL = "__GENEALOGY_DIR__"  # Distinct from the real "GENEALOGY_DIR" settings key
TOOLBOX_DIR_SENTINEL = "__TOOLBOX_DIR__"  # The Antiquarian code folder itself (BASE_DIR).

# ==========================================
# TOOLTIP DESCRIPTIONS
# ==========================================
TOOLTIP_DESCRIPTIONS = {  # Global Settings
    "GENEALOGY_DIR": "Your single base Genealogy folder. Everything else, including the Antiquarian code, your "
    "Roots Magic / Family Tree Maker databases, Media, and GEDCOM output, lives directly inside "
    "this one folder.",
    "AGY_MODEL_NAME": "The exact Antigravity CLI model ID (e.g. gemini-3.1-pro-high) - always passed explicitly on "
                      "every call. Antigravity's own default is a flash-tier model with noticeably lower OCR "
                      "quality, and shorthand values like 'pro' or 'flash' are not valid - only exact IDs from "
                      "`agy models` work.",
    "MEDIA_DIR": "The base folder where your genealogy media is stored.",
    "RM_DIR": "The folder where your RootsMagic files live, relative to the Genealogy Dir.",
    "JSON_DIR": "The folder where downloaded JSON data files are kept.",
    "GEDCOM_OUTPUT_PATH": "The folder where the finished, ready-to-import GEDCOM files will be saved.",
    "PROMPTS_DIR": "The folder (relative to the Genealogy Root Directory) holding your .pmt record-type "
                   "prompt files. Paleographer/Archivist look here first, then fall back to the copy bundled "
                   "with the app - override this only if you keep customized .pmt files of your own.",
    "RESEARCHER": "Your name. This will be added to the GEDCOM file to give you credit as the transcriber.",
    "ORG_NAME": "The name of your Historical Society, Library, or personal organization to include in GEDCOM headers.",
    "ROOT_SOURCE_ID": "The master SOUR (Source) ID used in RootsMagic for the researcher credit (e.g., @S1@).",
    "FTM_DIR": "The folder where your Family Tree Maker files live, relative to the Genealogy Dir.",
    "SUBM_ADDRESS": "A contact URL or email address for the submitter/researcher.",
    "MGS_GROUP_URL": "The URL for the MGS online group or repository (if applicable).",
    "ANCESTRY_GROUP_URL": "The URL for the corresponding Ancestry group or page (if applicable).",
    "API_BUDGET": "Your configured maximum API spend budget for transcription tasks.",
    "COST_PER_1M_INPUT": "The cost per 1 million input tokens for the AI model.",
    "COST_PER_1M_OUTPUT": "The cost per 1 million output tokens for the AI model.",
    "CACHE_DISCOUNT_MULTIPLIER": "The discount multiplier applied when prompts match the cached context."}

# ==========================================
# CUSTOM UI LABELS OVERRIDE
# ==========================================
# Add keys here if you want them to display differently than standard Title Case.
CUSTOM_LABELS = {
    "GENEALOGY_DIR": "Genealogy Root Directory",
    "RM_DIR": "RootsMagic Folder",
    "FTM_DIR": "Family Tree Maker Folder",
    "MEDIA_DIR": "Base Media Directory",
    "JSON_DIR": "JSON Download Folder",
    "PROMPTS_DIR": "Prompts Folder",
    "AGY_MODEL_NAME": "Antigravity Model Name"}

# ==========================================
# PATH & FILE PICKER FIELDS
# ==========================================
# Keys that get a "Browse..." button next to their entry, opening a native dialog instead of
# requiring the value to be typed by hand. "kind" picks the dialog: "directory" for folder
# fields, "open" for picking an existing file, "save" for naming a new/output file (lets you
# type a name that doesn't exist yet). "base_dir_key" says which folder the dialog should
# start in: another field's key (resolved against PROGRAM_DIR, same as execute_script does),
# or one of the two sentinels below.
PATH_PICKER_FIELDS = {
    # Global: Directories (folders, relative to GENEALOGY_DIR unless absolute)
    "GENEALOGY_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL, "always_absolute": True},
    "RM_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
    "FTM_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
    "MEDIA_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
    "JSON_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
    "GEDCOM_OUTPUT_PATH": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
    "PROMPTS_DIR": {"kind": "directory", "base_dir_key": GENEALOGY_DIR_SENTINEL},
}

# ==========================================
# RICHER FIELD WIDGETS (toggles/segments/sliders instead of plain text entries)
# ==========================================
# Keyed by settings key; any key not listed here keeps the default plain CTkEntry behavior.
FIELD_WIDGETS = {
}


# ==========================================
# PER-TOOL SETTINGS SCHEMA LOADER
# ==========================================
def load_tool_schema(tool_dir: Path) -> Dict[str, Dict[str, str]]:
    """Loads one tool's settings_schema.yaml, returning the same {section: {key: default}}
    shape the hardcoded *_VARS dict literals used to provide directly. As a side effect,
    merges this tool's tooltips/widgets/pickers/label-overrides into the shared
    module-level dicts _build_form_ui already reads from - so nothing downstream of this
    call needs to know settings moved from a Python literal to a YAML file. Fails loudly:
    this is a single-user desktop tool, so a missing or malformed schema should stop
    startup with a message naming the file, not silently render an empty or partial form."""
    schema_path = tool_dir / "settings_schema.yaml"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Missing settings schema: {schema_path}")

    try:
        raw = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise RuntimeError(f"Malformed YAML in {schema_path}: {e}") from e

    if not isinstance(raw, dict) or "sections" not in raw:
        raise ValueError(f"{schema_path} is missing its top-level 'sections' key")

    result: Dict[str, Dict[str, str]] = {}
    for section, fields in raw["sections"].items():
        if not isinstance(fields, dict):
            raise ValueError(f"{schema_path}: section '{section}' is not a mapping")
        result[section] = {}
        for field, spec in fields.items():
            if not isinstance(spec, dict) or "default" not in spec:
                raise ValueError(f"{schema_path}: field '{section}.{field}' is missing a 'default'")
            result[section][field] = str(spec["default"])

            if "tooltip" in spec:
                TOOLTIP_DESCRIPTIONS[field] = spec["tooltip"]

            if "widget" in spec:
                widget_spec = {"type": spec["widget"]}
                if "options" in spec:
                    widget_spec["options"] = [tuple(opt) for opt in spec["options"]]
                for extra_key in ("min", "max", "step", "suffix"):
                    if extra_key in spec:
                        widget_spec[extra_key] = spec[extra_key]
                FIELD_WIDGETS[field] = widget_spec

            if "picker" in spec:
                picker_spec = dict(spec["picker"])
                if "filetypes" in picker_spec:
                    picker_spec["filetypes"] = [tuple(ft) for ft in picker_spec["filetypes"]]
                PATH_PICKER_FIELDS[field] = picker_spec

    for field, label in (raw.get("label_overrides") or {}).items():
        CUSTOM_LABELS[field] = label

    return result


_load_tool_schema = load_tool_schema

ARCHIVIST_VARS = load_tool_schema(BASE_DIR / "Archivist")

# ==========================================
# VOYAGEUR SOURCES
# ==========================================
# Each source is one Voyageur sub-script (Voyageur/<code>.py); adding a new Major Repository
# is exactly this - a new sub-script plus one more entry here, nothing else touched.
VOYAGEUR_SOURCES = [
    ("A", "Ancestry"),
    ("FS", "FamilySearch"),
    ("LAC", "LAC"),
    ("HBCA", "Keystone Archives"),
]

VOYAGEUR_VARS = load_tool_schema(BASE_DIR / "Voyageur")

PALEOGRAPHER_VARS = load_tool_schema(BASE_DIR / "Paleographer")

REGISTRAR_VARS = load_tool_schema(BASE_DIR / "Registrar")


GAZETTEER_VARS = load_tool_schema(BASE_DIR / "Gazetteer")

PDFIX_VARS = load_tool_schema(BASE_DIR / "PDFix")


# ==========================================
# ENV FILE TARGETS
# ==========================================
# Global settings persist to the project root's .env. Each tool's own settings persist to a
# .env file inside that tool's own subfolder, so every tool stays runnable standalone.
ENV_TARGETS = [(GLOBAL_VARS, None),
               (ARCHIVIST_VARS, "Archivist"),
               (PALEOGRAPHER_VARS, "Paleographer"),
               (VOYAGEUR_VARS, "Voyageur"),
               (REGISTRAR_VARS, "Registrar"),
               (GAZETTEER_VARS, "Gazetteer"),
               (PDFIX_VARS, "PDFix")]


# ==========================================
# CUSTOM WIDGET CLASSES
# ==========================================
class ToolTip:
    """Creates a hover tooltip for a given widget, bypassing CtkToplevel bugs using pure tkinter."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave)

    def enter(self, _event=None):
        self.schedule()

    def leave(self, _event=None):
        self.unschedule()
        self.hide()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(400, self.show)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            try:
                self.widget.after_cancel(id_)
            except (ValueError, tk.TclError):
                pass

    def show(self):
        self.unschedule()

        # Safety Check 1: Ensure mouse is strictly inside the widget bounds before drawing
        try:
            x, y = self.widget.winfo_pointerxy()
            wx_root = self.widget.winfo_rootx()
            wy_root = self.widget.winfo_rooty()
            w_width = self.widget.winfo_width()
            w_height = self.widget.winfo_height()

            if not (wx_root <= x <= wx_root + w_width and wy_root <= y <= wy_root + w_height):
                return
        except tk.TclError:
            pass

        def tip_pos_calculator(w_widget, tip_label, *, tip_delta=(10, 15), pad=(5, 3, 5, 3)):
            s_width, s_height = w_widget.winfo_screenwidth(), w_widget.winfo_screenheight()
            width, height = (pad[0] + tip_label.winfo_reqwidth() + pad[2],
                             pad[1] + tip_label.winfo_reqheight() + pad[3])
            mouse_x, mouse_y = w_widget.winfo_pointerxy()
            x1, y1 = mouse_x + tip_delta[0], mouse_y + tip_delta[1]
            x2, y2 = x1 + width, y1 + height

            x_delta = x2 - s_width
            if x_delta < 0:
                x_delta = 0
            y_delta = y2 - s_height
            if y_delta < 0:
                y_delta = 0

            offscreen = (x_delta, y_delta) != (0, 0)
            if offscreen:
                if x_delta:
                    x1 = mouse_x - tip_delta[0] - width
                if y_delta:
                    y1 = mouse_y - tip_delta[1] - height
            return x1, y1

        self.hide()

        # FIX: We use a raw tkinter Toplevel instead of CtkToplevel.
        # Ctk intercepts overrideredirect focus events and causes ghost windows.
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        if sys.platform == 'darwin':
            tw.wm_attributes('-transparent', True)

        # Build the tooltip label
        label = ctk.CTkLabel(tw, text=self.text, justify="left", fg_color="#1a1a1a", text_color="#E0E0E0",
                             corner_radius=8, padx=12, pady=8, font=ctk.CTkFont(size=12))
        label.pack()

        # Position it next to the cursor
        x, y = tip_pos_calculator(self.widget, label)
        tw.wm_geometry(f"+{x}+{y}")

        # Safety Check 2: If the mouse accidentally wanders INTO the tooltip, kill it.
        tw.bind("<Leave>", self.leave)

    def hide(self):
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            try:
                tw.destroy()
            except tk.TclError:
                pass


class ConsoleRedirector:
    """Manages UI updates for streamed console text, routing progress bars appropriately."""

    def __init__(self, text_widget, status_widget, on_progress=None, on_line=None):
        self.text_widget = text_widget
        self.status_widget = status_widget
        self.on_progress = on_progress
        self.on_line = on_line
        self.queue = queue.Queue()
        self.ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        # Paleographer prints "[3/45] Processing file.jpg..." with end="" (no newline),
        # so a single 50ms tick may only see a few characters of it - this buffer
        # accumulates raw text across ticks so the regex still matches once it's complete.
        self._progress_buffer = ""
        self._progress_re = re.compile(r'\[(\d+)/(\d+)\] Processing')
        self.update_gui()

    def put(self, text):
        self.queue.put(text)

    def update_gui(self):
        """Routes transient progress bars to the top entry, and permanent logs below."""
        if self.queue.empty():
            self.text_widget.after(50, self.update_gui)
            return

        self.text_widget.configure(state="normal")

        chars = []
        while not self.queue.empty():
            chars.append(self.queue.get_nowait())

        if chars:
            text_chunk = "".join(chars)
            clean_chunk = self.ansi_escape.sub('', text_chunk)
            clean_chunk = clean_chunk.replace('\r\n', '\n')

            if self.on_progress is not None:
                self._progress_buffer = (self._progress_buffer + clean_chunk)[-500:]
                matches = list(self._progress_re.finditer(self._progress_buffer))
                if matches:
                    current, total = matches[-1].groups()
                    self.on_progress(int(current), int(total))

            if '\r' in clean_chunk:
                parts = clean_chunk.split('\r')
                for i, part in enumerate(parts):
                    if i == 0 and part:
                        self.text_widget.insert("end", part)
                    elif i > 0:
                        if '\n' in part:
                            log_parts = part.rsplit('\n', 1)
                            if log_parts[0]:
                                self.text_widget.insert("end", log_parts[0] + '\n')
                            if len(log_parts) > 1 and log_parts[1]:
                                self.status_widget.configure(state="normal")
                                self.status_widget.delete(0, "end")
                                self.status_widget.insert(0, log_parts[1])
                                self.status_widget.configure(state="readonly")
                        else:
                            self.status_widget.configure(state="normal")
                            self.status_widget.delete(0, "end")
                            self.status_widget.insert(0, part)
                            self.status_widget.configure(state="readonly")
            else:
                self.text_widget.insert("end", clean_chunk)

            if self.on_line is not None:
                lines = [ln for ln in clean_chunk.replace('\r', '\n').split('\n') if ln.strip()]
                if lines:
                    self.on_line(lines[-1])

        try:
            current_lines = int(self.text_widget.index('end-1c').split('.')[0])
            if current_lines > 1500:
                self.text_widget.delete("1.0", f"{current_lines - 1500}.0")
        except (ValueError, TypeError, AttributeError):
            pass

        self.text_widget.see("end")
        self.text_widget.configure(state="disabled")
        self.text_widget.after(50, self.update_gui)


# App window/taskbar icon (a stylized pen nib), embedded as base64 PNG data rather than a
# file under Commissioner/assets/ - tkinter's iconphoto() takes PhotoImage objects built straight from
# in-memory data, so the icon has no external file to go missing or need bundling. Two
# sizes are supplied; Tk picks whichever fits a given context (taskbar, alt-tab, title bar).
_APP_ICON_PNG_32 = (
    "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"
    "TkSuQmCC"
)

_APP_ICON_PNG_64 = (
    "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"
)


class Antiquarian(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("The Antiquarian")

        # iconphoto() (unlike iconbitmap) takes PhotoImage objects built directly from
        # in-memory PNG data - no .ico file needed on disk. Tk keeps only a weak reference
        # to PhotoImage objects internally, so they're stashed on self to outlive this call;
        # letting them get garbage-collected would silently blank the icon later. Wrapped
        # defensively since window-icon support varies by platform/Tk build and this is
        # purely cosmetic - never worth failing startup over.
        try:
            self._app_icons = [tk.PhotoImage(data=_APP_ICON_PNG_32), tk.PhotoImage(data=_APP_ICON_PNG_64)]
            self.iconphoto(True, *self._app_icons)
        except Exception:
            pass

        # Sized as a fraction of the actual monitor instead of a fixed pixel size, so it
        # scales sensibly on anything from a small laptop panel to a large desktop display.
        # Deliberately NOT full-screen-height: winfo_screenwidth/height() report the raw
        # display resolution, which includes whatever the taskbar covers, so a window sized
        # too close to that full height renders with its bottom edge underneath the taskbar
        # when not maximized (confirmed live earlier this session with an unrelated preview
        # window). A Win32 SPI_GETWORKAREA call could get the exact taskbar-free region, but
        # tried and reverted: CustomTkinter's own __init__ (just above) changes the
        # process's DPI-awareness state, which then makes that raw ctypes call report
        # physical pixels while Tk's own winfo_screenwidth/height keeps reporting in its own
        # logical space - the two disagreed enough to produce a garbage tiny window. Staying
        # entirely within Tk's own self-consistent measurements and leaving a generous
        # margin is more robust than chasing the exact taskbar height.
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()

        window_width = max(1000, min(1600, int(screen_width * 0.82)))
        window_height = max(650, min(900, int(screen_height * 0.82)))
        center_x = (screen_width - window_width) // 2
        center_y = (screen_height - window_height) // 2

        self.geometry(f"{window_width}x{window_height}+{center_x}+{center_y}")
        self.minsize(1000, 600)  # Prevents scrollbars from squishing to 0 height and crashing

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme(str(BASE_DIR / "Commissioner" / "assets" / "theme.json"))

        self.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.string_vars: Dict[str, ctk.StringVar] = {}
        # Per-key state ("default"/"blank"/"user") so the settings form can visually tell
        # apart an untouched placeholder, a field deliberately left empty and saved that
        # way, and a real user-entered value - see _build_form_ui's field-state styling.
        self.field_state: Dict[str, str] = {}
        self.active_process = None
        self._cancel_requested = False
        self.debug_file_var = ctk.StringVar(value="")
        self.tabs_built = set()

        self.help_texts = {"Voyageur": "Welcome to Voyageur!\n\n"
                           "Voyageur is the Gather step: it talks to a repository's website and "
                           "brings back whatever it has, images plus any index data the site "
                           "already provides.\n\n"
                           "How to use:\n"
                           "1. Pick which repository to gather from in the dropdown (Ancestry, "
                           "FamilySearch, or LAC for now, more to come).\n"
                           "2. Paste the record/collection URL for that repository into its "
                           "settings box.\n"
                           "3. Click the gather button. Ancestry and FamilySearch open your "
                           "browser and drive a Tampermonkey script there; LAC downloads "
                           "directly.\n\n"
                           "Once gathering finishes, head to Paleographer (if the images need AI "
                           "transcription) or straight to Archivist to build your GEDCOM.\n\n"
                           "\"Gather and Send to Archivist\" runs the gather and then automatically "
                           "builds the GEDCOM as soon as it finishes cleanly, in one click - skip "
                           "this if the images still need Paleographer's AI transcription first.",
                           "Paleographer": "Welcome to Paleographer!\n\n"
                           "Paleographer is the Analysis and Enrichment step: it reads historical document images "
                           "and turns them into structured data using AI, and provides metadata enrichment and "
                           "collection partitioning for Scrip datasets.\n\n"
                           "How to use:\n"
                           "1. Pick a record type from the dropdown (Parish, Scrip, or any other "
                           ".pmt file you've added to Paleographer/prompts).\n"
                           "2. Place your historical document images or PDFs into that type's "
                           "designated folder in your project.\n"
                           "3. Ensure you have your AI API key saved in the Global Settings.\n"
                           "4. Click 'Run Analysis (API)' to transcribe. For Scrip records, use 'Enrich Metadata' "
                           "to fetch live LAC catalog metadata, 'Partition Collections' to split records into "
                           "official LAC archival series files, or 'Resolve Names' to cross-reference and "
                           "deduplicate participant names across records.\n\n"
                           "When finished, head to Archivist to build your GEDCOM.\n\n"
                           "Note: If the AI gets stuck or runs out of memory, try clicking "
                           "'Clear Cache'.",
                           "Archivist": "Welcome to Archivist!\n\n"
                           "Archivist is the Create step: the single place that turns a finished "
                           "JSON file, from Voyageur's Gather or Paleographer's Analysis, into a "
                           "GEDCOM file you can import.\n\n"
                           "How to use:\n"
                           "1. Check your settings (image folder, location overrides, etc).\n"
                           "2. Click 'Generate GEDCOM'. Archivist reads whichever JSON is currently "
                           "configured, automatically detects what kind of record it holds "
                           "(census, church/parish, scrip...), and builds the right GEDCOM without "
                           "you needing to pick a mode.",
                           "Registrar": "Welcome to the Registrar!\n\n"
                           "How to use:\n"
                           "This tool scans your RootsMagic tree for people who might be "
                           "duplicated, using smart name and age matching.\n\n"
                           "1. CRITICAL: Make sure RootsMagic is completely CLOSED before running "
                           "this.\n"
                           "2. Click 'Run Script' and follow the prompts in the console below.\n"
                           "3. The tool will safely create 'Review Merge' tasks inside your "
                           "RootsMagic database. Open RootsMagic and check your Task List to "
                           "see the results!",
                           "Gazetteer": "Welcome to the Gazetteer!\n\n"
                                           "How to use:\n"
                                           "This tool looks at the dates of events in your tree and automatically "
                                           "corrects the County or Territory names to match historical boundaries "
                                           "for that exact year.\n\n"
                                           "1. CRITICAL: Make sure RootsMagic is completely CLOSED before running "
                                           "this.\n"
                                           "2. Make sure you have backed up your tree.\n"
                                           "3. Click 'Run Script'. It will update the display names of your places "
                                           "safely without breaking your maps or tracking IDs.",
                           "PDFix": "Welcome to PDFix!\n\n"
                           "This tool losslessly shrinks the file size of every PDF in a folder (and its "
                           "subfolders), by removing dead internal structure and re-compressing streams. "
                           "It never rescales embedded image resolution.\n\n"
                           "1. Set your PDF Scan Folder, relative to your Base Media Directory.\n"
                           "2. Leave 'Create Backup' on unless you're confident - it rewrites PDFs in "
                           "place.\n"
                           "3. Click 'Run Script' and follow along in the console below.",
                           "Global Settings": "Welcome to Global Settings!\n\n"
                                              "How to use:\n"
                                              "These are the master settings shared across all of your tools.\n\n"
                                              "1. Set your 'GENEALOGY_DIR' first. This is the main folder for your "
                                              "genealogy files. All other folder paths build off of this one.\n"
                                              "2. Add your AI API Key here so the AI transcription tool can "
                                              "function.\n"
                                              "3. Update your name and organization so the GEDCOM files properly "
                                              "credit your research.\n"
                                              "4. Don't forget to click 'Save Global Config' when you make changes!"}

        self.tab_builders: Dict[str, Callable[[ctk.CTkFrame], None]] = {"Voyageur": self._build_tab_voyageur,
                                                                        "Paleographer": self._build_tab_paleographer,
                                                                        "Archivist": self._build_tab_archivist,
                                                                        "Registrar": self._build_tab_registrar,
                                                                        "Gazetteer": self._build_tab_gazetteer,
                                                                        "PDFix": self._build_tab_pdfix,
                                                                        "Global Settings": self._build_tab_global}

        self._build_layout()
        self._load_env_to_vars()

        # Force a geometry update before building the first tab.
        # This fixes a known CustomTkinter bug where CTkScrollableFrame
        # crashes with a math error if drawn before the window has a height.
        self.update_idletasks()
        self._switch_tab("Voyageur")

        global_env = dotenv_values(env_path_for(None))
        if global_env.get("TAMPERMONKEY_INSTALLED") != "True":
            self.after(500, self._prompt_tampermonkey)

        # Check for new releases asynchronously to avoid blocking the GUI thread on startup.
        threading.Thread(target=self._auto_update_check, daemon=True).start()

    def _auto_update_check(self):
        try:
            import urllib.request
            import json
            req = urllib.request.Request(
                "https://api.github.com/repos/alerum68/Antiquarian/releases/latest",
                headers={'User-Agent': 'Antiquarian-App'}
            )
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                latest_tag = data.get("tag_name", "")

                def parse_ver(v):
                    m = re.search(r'(\d+)\.(\d+)\.(\d+)', v)
                    return tuple(map(int, m.groups())) if m else (0, 0, 0)

                current = parse_ver(APP_VERSION)
                latest = parse_ver(latest_tag)

                if latest > current:
                    self.after(2000, lambda: self._show_update_popup(latest_tag, data.get("html_url", "")))
        except Exception as e:
            print(f"Update check failed: {e}")

    def _show_update_popup(self, version, url):
        msg = f"A new version of Antiquarian ({version}) is available!\n\nWould you like to download it now?"
        if messagebox.askyesno("New Version Available", msg):
            import webbrowser
            webbrowser.open(url)

    def _prompt_tampermonkey(self):
        msg = ("Antiquarian requires the Tampermonkey browser extension to gather Ancestry records.\n\n"
               "Would you like to install it and the Voyageur script now?")
        if messagebox.askyesno("Tampermonkey Required", msg):
            webbrowser.open("https://www.tampermonkey.net/")
            # Voyageur.js ships alongside the app itself (build.py copies it to Sys/), not
            # under the user's Genealogy directory - same PROGRAM_DIR-relative reasoning as
            # Gazetteer's shapefiles.
            js_path = APP_DIR / "Sys" / "Voyageur.js"
            if js_path.exists():
                try:
                    os.startfile(js_path)
                except Exception as e:
                    print(f"Could not open Voyageur.js: {e}")
            batch_set_env(env_path_for(None), {"TAMPERMONKEY_INSTALLED": "True"})

    def _on_closing(self):
        """Forcefully terminates the window and kills any zombie threads running in background."""
        if self.active_process and self.active_process.poll() is None:
            try:
                self.active_process.terminate()
                self.active_process.kill()
            except (OSError, subprocess.SubprocessError):
                pass
        self.destroy()
        sys.exit(0)

    def _build_layout(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.main_container = ctk.CTkFrame(self, corner_radius=10)
        self.main_container.grid(row=0, column=0, padx=20, pady=20, sticky="nsew")

        self.main_container.grid_rowconfigure(0, weight=1)
        self.main_container.grid_columnconfigure(0, weight=0)  # sidebar: fixed width
        self.main_container.grid_columnconfigure(1, weight=1)  # main panel: expands

        self._build_sidebar(self.main_container)

        self.main_panel = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.main_panel.grid(row=0, column=1, sticky="nsew")
        self.main_panel.grid_columnconfigure(0, weight=1)
        self.main_panel.grid_rowconfigure(2, weight=1)  # content_area is the only row that expands

        # Sits above content_area, so it stays visible - and reflects the truth - no matter
        # which tab is active when a background job is running.
        self.status_row = ctk.CTkFrame(self.main_panel, fg_color="transparent")
        self.status_row.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        self.status_row.grid_columnconfigure(1, weight=1)

        self.run_indicator = ctk.CTkLabel(self.status_row, text="●", width=20,
                                          font=ctk.CTkFont(size=16, weight="bold"), text_color=C_TEXT_MUTED)
        self.run_indicator.grid(row=0, column=0, padx=(0, 5))
        self.run_tooltip = ToolTip(self.run_indicator, "Idle - no script running")

        self.status_bar = ctk.CTkEntry(self.status_row, font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
                                       text_color=C_ACCENT, fg_color=C_SURFACE_RAISED, border_width=1,
                                       border_color=C_BORDER)
        self.status_bar.grid(row=0, column=1, sticky="ew")
        self.status_bar.insert(0, "System Ready")
        self.status_bar.configure(state="readonly")

        # Reopens/dismisses the pop-up console at any time, not just while a script is
        # running - lives in status_row (outside content_area) so _recursive_state's
        # disable-while-running pass never touches it.
        self.console_toggle_btn = ctk.CTkButton(self.status_row, text="Console", width=90, height=28,
                                                fg_color="transparent", border_width=1, border_color=C_BORDER,
                                                text_color=C_TEXT, hover_color=C_SURFACE_RAISED,
                                                command=self._toggle_console)
        self.console_toggle_btn.grid(row=0, column=2, padx=(8, 0))

        # A dedicated row (not just a bare progress bar) so it can carry an explicit height
        # and a live percentage readout - a plain default-height CTkProgressBar reads as
        # nearly invisible against the surface color.
        self.progress_row = ctk.CTkFrame(self.main_panel, fg_color="transparent")
        self.progress_row.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 0))
        self.progress_row.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(self.progress_row, mode="determinate", height=16,
                                               progress_color=C_ACCENT, fg_color=C_SURFACE)
        self.progress_bar.set(0)
        self.progress_bar.grid(row=0, column=0, sticky="ew")

        self.progress_pct_label = ctk.CTkLabel(self.progress_row, text="0%", width=36,
                                               font=ctk.CTkFont(size=12, weight="bold"), text_color=C_TEXT_MUTED)
        self.progress_pct_label.grid(row=0, column=1, padx=(10, 0))

        self.progress_row.grid_remove()  # hidden until a run reports real [i/total] progress

        # --- content area: one frame per tab, created lazily on first visit and tkraise()'d
        # by _switch_tab instead of a CTkTabview tab strip. ---
        self.content_area = ctk.CTkFrame(self.main_panel, fg_color="transparent")
        self.content_area.grid(row=2, column=0, sticky="nsew", padx=10, pady=(5, 5))
        self.content_area.grid_rowconfigure(0, weight=1)
        self.content_area.grid_columnconfigure(0, weight=1)
        self.content_area.bind("<Configure>", self._on_content_area_resize)

        self.tab_frames: Dict[str, ctk.CTkFrame] = {}
        # Keyed by each tab's own content frame -> that tab's _build_tab_header() frame, so
        # the console overlay below can measure exactly how tall THIS tab's title+description
        # rendered (it varies - description length differs per tab) and cover only the body
        # beneath it, never the header itself.
        self.tab_header_frames: Dict[ctk.CTkFrame, ctk.CTkFrame] = {}
        # Keyed the same way, but -> that tab's bottom-docked action-button row (see
        # _create_action_box), so the console overlay can also stop short of covering it -
        # otherwise the overlay spans the whole body and hides the very buttons a user would
        # need to re-run a script without first closing the console.
        self.tab_btn_boxes: Dict[ctk.CTkFrame, ctk.CTkFrame] = {}

        # --- pop-up console: hidden by default, raised over the active tab's body (leaving
        # that tab's own header visible) the moment execute_script starts a job. A sibling of
        # the tab frames inside content_area, shown/hidden via place()/place_forget() instead
        # of grid, so it never claims a grid slot of its own. ---
        self.console_overlay = ctk.CTkFrame(self.content_area, fg_color=C_SURFACE, corner_radius=8,
                                            border_width=1, border_color=C_ACCENT)
        self.console_overlay.grid_rowconfigure(1, weight=1)
        self.console_overlay.grid_columnconfigure(0, weight=1)
        self._console_expanded = False

        overlay_hdr = ctk.CTkFrame(self.console_overlay, fg_color="transparent")
        overlay_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ctk.CTkLabel(overlay_hdr, text="Console", font=ctk.CTkFont(size=14, weight="bold"),
                     text_color=C_TEXT).pack(side="left")
        self._console_status_label = ctk.CTkLabel(
            overlay_hdr, text="", font=ctk.CTkFont(size=12), text_color=C_TEXT_MUTED)
        self._console_status_label.pack(side="left", padx=(8, 0))
        # A click-bound label, not a CTkButton - _recursive_state only disables CTkButtons
        # while a script runs, so this stays clickable to dismiss the overlay mid-run.
        back_label = ctk.CTkLabel(overlay_hdr, text="Back to Settings  ▲", font=ctk.CTkFont(size=12, weight="bold"),
                                  text_color=C_ACCENT, cursor="hand2")
        back_label.pack(side="right")
        back_label.bind("<Button-1>", self._toggle_console)

        # Set fixed fallback dimensions to prevent 0-height rendering geometry crash
        self.console_text = ctk.CTkTextbox(self.console_overlay, font=ctk.CTkFont(family="Consolas", size=12),
                                           text_color=C_SUCCESS, width=800, height=220)
        self.console_text.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 10))
        self.console_text.configure(state="disabled")

        self.input_frame = ctk.CTkFrame(self.console_overlay, fg_color="transparent")
        self.input_frame.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 18))
        self.input_frame.grid_columnconfigure(0, weight=1)

        self.console_input = ctk.CTkEntry(self.input_frame, height=32,
                                          placeholder_text="Type script input here and press Enter...",
                                          state="disabled")
        self.console_input.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.console_input.bind("<Return>", self.send_input)

        self.cancel_btn = ctk.CTkButton(self.input_frame, text="Cancel", fg_color=C_DANGER, hover_color=C_DANGER_HOVER,
                                        text_color=C_ON_ACCENT, width=80, height=32,
                                        state="disabled", command=self.cancel_script)
        self.cancel_btn.grid(row=0, column=1)

        self.console = ConsoleRedirector(self.console_text, self.status_bar,
                                         on_progress=self._on_progress_update, on_line=self._on_console_line)

    def _build_sidebar(self, parent):
        """Left nav rail replacing the old top CTkTabview: a plain (non-scrolling) list of
        the pipeline tools (Voyageur/Paleographer/Archivist, ungrouped) plus a labeled
        Utilities group (Registrar/Gazetteer/PDFix) and a footer pinned to the bottom (Help,
        Global Settings). Text-only nav items - no icons, no pipeline connector marks.
        A plain frame (not CTkScrollableFrame) for the nav list - six items plus one group
        label comfortably fit the sidebar's height without ever needing to scroll."""
        # width= + grid_propagate(False): without this, CTkFrame shrinks itself to fit its
        # children's natural size regardless of sticky="nsew", so the dark background
        # wouldn't fill the sidebar's full column.
        self.sidebar = ctk.CTkFrame(parent, fg_color=C_SURFACE, corner_radius=8, width=210)
        self.sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.sidebar.grid_propagate(False)

        self.nav_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.nav_frame.pack(side="top", fill="x", padx=8, pady=(14, 4))

        ctk.CTkLabel(self.nav_frame, text="Antiquarian", font=ctk.CTkFont(family="Georgia", size=17, weight="bold")
                     ).pack(anchor="w", padx=6, pady=(0, 14))

        self._nav_buttons: Dict[str, ctk.CTkButton] = {}

        def add_group_label(text):
            ctk.CTkLabel(self.nav_frame, text=text.upper(), font=ctk.CTkFont(size=10, weight="bold"),
                         text_color=C_TEXT_MUTED).pack(anchor="w", padx=6, pady=(10, 4))

        def add_nav_button(container, tab_name, command=None):
            btn = ctk.CTkButton(container, text=tab_name, anchor="w", fg_color="transparent",
                                hover_color=C_SURFACE_RAISED, text_color=C_TEXT,
                                command=command or partial(self._switch_tab, tab_name))
            btn.pack(fill="x", pady=1)
            self._nav_buttons[tab_name] = btn
            return btn

        add_nav_button(self.nav_frame, "Voyageur")
        add_nav_button(self.nav_frame, "Paleographer")
        add_nav_button(self.nav_frame, "Archivist")

        add_group_label("Utilities")
        add_nav_button(self.nav_frame, "Registrar")
        add_nav_button(self.nav_frame, "Gazetteer")
        add_nav_button(self.nav_frame, "PDFix")

        self.sidebar_footer = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.sidebar_footer.pack(side="bottom", fill="x", padx=8, pady=(4, 14))
        ctk.CTkFrame(self.sidebar_footer, height=1, fg_color=C_BORDER).pack(fill="x", pady=(0, 6))
        add_nav_button(self.sidebar_footer, "Help", command=self._show_current_help)
        # "Global Settings" doubles as both a real tab_name (drives _switch_tab / active-
        # highlight bookkeeping the same as any other nav button) and its own settings form.
        add_nav_button(self.sidebar_footer, "Global Settings")

    def _switch_tab(self, tab_name):
        """Replaces CTkTabview's own tab switching: lazily creates a tab's own frame and
        builds its content into it on first visit, raises it, and updates which sidebar
        nav button reads as active."""
        self.current_tab_name = tab_name
        if tab_name not in self.tab_frames:
            frame = ctk.CTkFrame(self.content_area, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self.tab_frames[tab_name] = frame
        frame = self.tab_frames[tab_name]
        if tab_name not in self.tabs_built:
            self.tab_builders[tab_name](frame)
            self.tabs_built.add(tab_name)
        frame.tkraise()
        if self._console_expanded:
            self._console_expanded = False
            self.console_overlay.place_forget()
        for name, btn in self._nav_buttons.items():
            if name == tab_name:
                btn.configure(fg_color=C_ACCENT, text_color=C_ON_ACCENT)
            elif name != "Help":
                btn.configure(fg_color="transparent", text_color=C_TEXT)

    switch_tab = _switch_tab

    def _show_current_help(self):
        """The sidebar's single Help entry point - always shows whichever tab is actually
        on screen right now."""
        self.show_help(getattr(self, "current_tab_name", "Voyageur"))

    def _toggle_console(self, _event=None):
        self._console_expanded = not self._console_expanded
        if self._console_expanded:
            self._show_console_overlay()
        else:
            self.console_overlay.place_forget()

    def _show_console_overlay(self):
        """Positions the pop-up console to cover the active tab's body while leaving that
        tab's own title/description header visible above it, and that tab's bottom-docked
        action-button row visible below it - so a script can be re-run without first
        closing the console. Both are measured live (not hardcoded), since header height
        (description text length) and button-row height (button count/wrapping) both vary
        per tab. CTk's place() rejects literal width/height (they can only be set at widget
        construction), so both offsets are expressed as relheight/relative fractions of
        content_area's own current height instead of pixel heights."""
        self.update_idletasks()
        tab_frame = self.tab_frames.get(self.current_tab_name)
        header = self.tab_header_frames.get(tab_frame)
        header_h = header.winfo_height() + 4 if header else 0
        btn_box = self.tab_btn_boxes.get(tab_frame)
        # btn_box is packed with pady=10 (see _create_action_box) - 10px reserved on both
        # its top AND bottom, not just the one side winfo_height() reports - plus a little
        # extra margin so a button's clickable area is never right up against the overlay's
        # edge even with the sub-pixel drift real widget geometry settles to.
        btn_box_h = btn_box.winfo_height() + 40 if btn_box and btn_box.winfo_ismapped() else 0
        content_h = self.content_area.winfo_height()
        frac = max(0.05, (content_h - header_h - btn_box_h) / content_h) if content_h > 0 else 1.0
        self.console_overlay.place(relx=0, rely=0, relwidth=1, relheight=frac, x=0, y=header_h)
        self.console_overlay.lift()

    def _on_content_area_resize(self, _event=None):
        """content_area's own <Configure> fires on window resize - reposition the overlay
        (its relheight fraction was computed from a specific content_area height, so it
        drifts as the window is resized) only while it's actually showing."""
        if self._console_expanded:
            self._show_console_overlay()

    def _expand_console(self):
        """Called by execute_script the moment a job actually starts, so first-run output
        is never silently missed behind a dismissed console - collapsing back is left to
        the user via the status row's Console toggle."""
        self._console_expanded = True
        self._show_console_overlay()

    def _on_console_line(self, line):
        self._console_status_label.configure(text=line[:100])

    def _on_progress_update(self, current, total):
        """Fed by ConsoleRedirector whenever it spots Paleographer's own
        "[i/total] Processing ..." line in the streamed output."""
        if total <= 0:
            return
        self.progress_row.grid()
        pct = min(1.0, current / total)
        self.progress_bar.set(pct)
        self.progress_pct_label.configure(text=f"{int(pct * 100)}%")

    def send_input(self, _event=None):
        if self.active_process and self.active_process.poll() is None:
            user_text = self.console_input.get()
            try:
                self.console.put(f"{user_text}\n")
                self.active_process.stdin.write((user_text + "\n").encode('utf-8'))
                self.active_process.stdin.flush()
                self.console_input.delete(0, 'end')
            except (OSError, BrokenPipeError, AttributeError) as e:
                self.console.put(f"\n[System] Failed to send input: {e}\n")

    def cancel_script(self):
        if self.active_process and self.active_process.poll() is None:
            self._cancel_requested = True
            self.console.put("\n[System] Sending termination signal to process...\n")
            self.active_process.terminate()

    def _load_env_to_vars(self):
        for category_dict, subfolder in ENV_TARGETS:
            saved = dotenv_values(env_path_for(subfolder))
            for fields in category_dict.values():
                for key, default_val in fields.items():
                    # `key in saved` (not `saved.get(key) or default_val`) - a key the user
                    # deliberately blanked and saved is present in the file with value "",
                    # which is falsy in Python; `or default_val` was silently reviving the
                    # placeholder default every time settings loaded, making it impossible
                    # to actually test/run with a field left blank on purpose. Only a key
                    # genuinely absent from the file (never saved at all) falls back to the
                    # placeholder default now.
                    val = saved[key] if key in saved else default_val
                    self.string_vars[key] = ctk.StringVar(value=val)
                    if key not in saved:
                        self.field_state[key] = "default"
                    elif saved[key] == "":
                        self.field_state[key] = "blank"
                    else:
                        self.field_state[key] = "user"

    def _save_env(self):
        for category_dict, subfolder in ENV_TARGETS:
            env_path = env_path_for(subfolder)
            updates: Dict[str, str] = {}
            for fields in category_dict.values():
                for key in fields.keys():
                    updates[key] = self.string_vars[key].get().replace('\\', '/')
            batch_set_env(env_path, updates)

        # Now that the user has deliberately chosen their paths, materialise the Prompts
        # folder so they know they can drop custom .pmt files there. All other data
        # directories (Media, JSON, GEDCOM) are created on first use by the tools themselves.
        gen_var = self.string_vars.get("GENEALOGY_DIR")
        gen_dir = gen_var.get().strip() if gen_var else ""
        prompts_var = self.string_vars.get("PROMPTS_DIR")
        prompts_subdir = (prompts_var.get().strip() if prompts_var else "") or "Prompts"
        if gen_dir:
            os.makedirs(os.path.join(gen_dir, prompts_subdir), exist_ok=True)

        self.console.put("\n[System] Environment variables saved (global settings to the root .env, "
                         "each tool's settings to its own subfolder).\n")

    def show_help(self, tab_name):
        """Displays a clean pop-up window with help instructions."""
        help_window = ctk.CTkToplevel(self)
        help_window.title(f"Help: {tab_name}")
        help_window.geometry("550x380")
        help_window.attributes('-topmost', True)  # Keeps the window easily accessible on top

        title = ctk.CTkLabel(help_window, text=f"How to use: {tab_name}", font=ctk.CTkFont(size=20, weight="bold"))
        title.pack(pady=(20, 10), padx=20, anchor="w")

        help_text = self.help_texts.get(tab_name, "Help information is unavailable.")

        textbox = ctk.CTkTextbox(help_window, wrap="word", font=ctk.CTkFont(size=14))
        textbox.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        textbox.insert("1.0", help_text)
        textbox.configure(state="disabled")  # Make read-only

    @staticmethod
    def _clean_label(key_str: str) -> str:
        """Converts UPPER_SNAKE_CASE to friendly Title Case, or uses a custom override."""
        if key_str in CUSTOM_LABELS:
            return CUSTOM_LABELS[key_str]

        # Handle some specific acronyms nicely
        cleaned = key_str.replace("URL", "Url").replace("JSON", "Json").replace("ID", "Id")
        cleaned = cleaned.replace("_", " ").title()
        return cleaned

    def _build_tab_header(self, frame: ctk.CTkFrame, title: str, role_tag: str, description: str):
        """A helper method to standardize tab headers and eliminate duplicate code."""
        header_frame = ctk.CTkFrame(frame, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 4))

        title_row = ctk.CTkFrame(header_frame, fg_color="transparent")
        title_row.pack(fill="x")
        ctk.CTkLabel(title_row, text=title, font=ctk.CTkFont(family="Georgia", size=24, weight="bold")
                     ).pack(side="left")
        ctk.CTkLabel(title_row, text=role_tag, font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                     fg_color=C_BRASS_SOFT, text_color=C_BRASS, corner_radius=4, padx=8, pady=2
                     ).pack(side="left", padx=(10, 0))
        # Outline/secondary style, not filled - this is a secondary action on every tab
        # (the tab's own primary action already saves everything too, via execute_script's
        # own _save_env() call), not the one thing this screen is for.
        ctk.CTkButton(title_row, text="Save Config", fg_color="transparent", hover_color=C_SURFACE_RAISED,
                      border_width=1, border_color=C_BRASS, text_color=C_BRASS,
                      command=self._save_env).pack(side="right", padx=5)

        ctk.CTkLabel(header_frame, text=description, font=ctk.CTkFont(size=13), text_color=C_TEXT_MUTED,
                     anchor="w", justify="left", wraplength=760).pack(fill="x", pady=(6, 0))

        # Recorded so the pop-up console overlay can measure this tab's actual rendered
        # header height (it varies - description length differs per tab) and cover only the
        # body beneath it, never the header itself.
        self.tab_header_frames[frame] = header_frame

    def _create_action_box(self, parent: ctk.CTkFrame) -> ctk.CTkFrame:
        """A helper method to standardize the action button frames and reduce code duplication."""
        btn_box = ctk.CTkFrame(parent, fg_color="transparent")
        btn_box.pack(side="bottom", fill="x", pady=10)  # Docked to bottom to prevent clipping
        # Recorded so the pop-up console overlay can measure this tab's actual rendered
        # button-row height and stop short of it - see tab_btn_boxes.
        self.tab_btn_boxes[parent] = btn_box
        return btn_box

    def _build_form_ui(self, parent, schema_dict, skip_keys: Optional[set] = None):
        # CTkScrollableFrame is a composite: what pack(fill="both", expand=True) actually
        # stretches is its outer wrapper frame (._parent_frame, via its overridden pack()),
        # which DOES correctly fill available space - confirmed live. But the visible
        # canvas *inside* that wrapper only shows however many pixels its own configured
        # height says to, and that never adapts to the *content* actually packed into it -
        # so a short form (a couple of collapsed sections) ends up as a mostly-empty
        # transparent canvas with a scrollbar nobody needs, rather than sizing to fit.
        # _resize_scroll below sizes the canvas to the CONTENT's own natural height instead
        # (capped so a fully-expanded, field-heavy tab still scrolls rather than growing
        # without limit) - the opposite of stretching to fill the parent.
        scroll = ctk.CTkScrollableFrame(parent, fg_color="transparent", width=800, height=200)
        scroll.pack(side="top", fill="both", expand=True, pady=10)
        skip_keys = skip_keys or set()

        def _resize_scroll(_event=None):
            parent.update_idletasks()
            natural_h = scroll.winfo_reqheight()
            scroll.configure(height=max(60, min(natural_h + 10, 600)))

        parent.bind("<Configure>", _resize_scroll)
        # A single early call can fire before the tab frame (built while still hidden
        # behind whichever tab was previously on top, then raised after) has settled -
        # confirmed live elsewhere in this file for a related sizing bug. Retried at
        # increasing delays (each call is idempotent) reliably catches whichever point
        # layout has actually settled.
        for delay in (50, 150, 350, 700):
            parent.after(delay, _resize_scroll)

        for section_index, (section, fields) in enumerate(schema_dict.items()):
            visible_keys = [k for k in fields.keys() if k not in skip_keys]
            if not visible_keys:
                continue

            # Divider line above every section after the first - the "dividers + subheaders"
            # pattern instead of a bordered box per section (see CHANGELOG: wrapping each
            # section in its own bordered CTkFrame is what caused Global Settings' sections
            # to render dead). A single 1px, unstyled CTkFrame - already used safely
            # elsewhere (the sidebar footer's own divider) - not the heavier
            # corner_radius+border_width construction that broke.
            if section_index > 0:
                ctk.CTkFrame(scroll, height=1, fg_color=C_BORDER).pack(fill="x", pady=(4, 0))

            header = ctk.CTkFrame(scroll, fg_color="transparent", cursor="hand2")
            header.pack(fill="x", pady=(15, 5))
            arrow_lbl = ctk.CTkLabel(header, text="", font=ctk.CTkFont(size=16, weight="bold"),
                                     text_color=C_ACCENT, width=16)
            arrow_lbl.pack(side="left")
            ctk.CTkLabel(header, text=section, font=ctk.CTkFont(size=16, weight="bold"),
                         text_color=C_ACCENT).pack(side="left")

            content = ctk.CTkFrame(scroll, fg_color="transparent")

            # First section open by default (most forms are short enough that this alone
            # is already usable); the rest start collapsed so a field-heavy tab isn't one
            # long wall of every setting scrolled past to find the one that matters.
            expanded = {"state": section_index == 0}

            def render_content():
                for key in visible_keys:
                    row = ctk.CTkFrame(content, fg_color="transparent")
                    row.pack(fill="x", pady=2)

                    # Generate hoverable, friendly labels
                    desc = TOOLTIP_DESCRIPTIONS.get(key)
                    friendly_name = self._clean_label(key)
                    display_text = f"{friendly_name} ⓘ" if desc else friendly_name

                    lbl = ctk.CTkLabel(row, text=display_text, width=250, anchor="w",
                                       cursor="hand2" if desc else "arrow")
                    lbl.pack(side="left", padx=5)

                    if desc:
                        ToolTip(lbl, desc)

                    widget_spec = FIELD_WIDGETS.get(key)

                    if widget_spec is None:
                        # Field-state styling: a value never explicitly saved (still just
                        # the placeholder default) renders dimmed, so it reads as a
                        # suggestion rather than real configuration; a deliberately-blanked,
                        # saved value gets an explicit hint instead of just looking like an
                        # empty box that was never filled in; a real saved value renders
                        # normally. Only meaningful for a plain text entry - a switch/
                        # slider/segment always shows a concrete position, with no real
                        # equivalent of a grayed-out placeholder.
                        state = self.field_state.get(key, "user")
                        entry = ctk.CTkEntry(row, textvariable=self.string_vars[key],
                                             text_color=C_TEXT_MUTED if state == "default" else None,
                                             placeholder_text="(intentionally blank)" if state == "blank" else "")
                        entry.pack(side="left", fill="x", expand=True, padx=5)

                        def _on_edit(_name, _index, _mode, k=key, e=entry):
                            # Once the user actually types something, this is no longer
                            # just a placeholder default sitting there unedited - promote
                            # it visually to a real, normal-colored value immediately, not
                            # just on next load.
                            if self.field_state.get(k) == "default":
                                self.field_state[k] = "user"
                                e.configure(text_color=None)

                        self.string_vars[key].trace_add("write", _on_edit)

                        picker = PATH_PICKER_FIELDS.get(key)
                        if picker:
                            ctk.CTkButton(row, text="Browse...", width=90,
                                          command=partial(self._browse_for_path, key, picker)
                                          ).pack(side="left", padx=5)
                    elif widget_spec["type"] == "toggle":
                        ctk.CTkSwitch(row, text="", variable=self.string_vars[key],
                                      onvalue="True", offvalue="False").pack(side="left", padx=5)
                    elif widget_spec["type"] == "segmented":
                        self._build_segmented_field(row, key, widget_spec)
                    elif widget_spec["type"] == "slider":
                        self._build_slider_field(row, key, widget_spec)

            render_content()

            # Every one of these must capture this iteration's own header/content/arrow_lbl/
            # expanded as default-argument values, not as ordinary closure-over-loop-variable
            # references - a plain `for` loop reuses the same local variable slots on every
            # pass, so a closure that only *reads* them (rather than binding them as defaults
            # at definition time) would have every section's click handler end up acting on
            # whichever section happened to be built last.
            def apply_expanded_state(exp=expanded, arr_lbl=arrow_lbl, cnt=content, hdr=header):
                arr_lbl.configure(text="▼" if exp["state"] else "▶")
                if exp["state"]:
                    cnt.pack(fill="x", after=hdr)
                else:
                    cnt.pack_forget()
                # The canvas is sized to whatever's actually showing (see _resize_scroll
                # above) - every collapse/expand changes that, so it has to be recomputed
                # every time, not just once at initial build.
                _resize_scroll()

            def toggle(_event=None, exp=expanded, fn_apply=apply_expanded_state):
                # header/arrow_lbl/every header child are all bound to this same handler
                # independently (see below) - confirmed live that the very first click of a
                # fresh session (before the window has ever had real OS focus) gets
                # delivered twice, flipping the section open then immediately shut again
                # (Windows can dispatch a window-activating click to more than one widget in
                # the hit-test chain). Never reproduces once the window already has focus.
                # A short debounce swallows that duplicate without needing to touch
                # window-activation/focus handling directly.
                now = time.monotonic()
                if now - exp.get("_last_toggle", 0.0) < 0.2:
                    return
                exp["_last_toggle"] = now
                exp["state"] = not exp["state"]
                fn_apply()

            header.bind("<Button-1>", toggle)
            arrow_lbl.bind("<Button-1>", toggle)
            for child in header.winfo_children():
                child.bind("<Button-1>", toggle)

            apply_expanded_state()

    def _build_segmented_field(self, row, key, widget_spec):
        """Renders a FIELD_WIDGETS "segmented" field: a small fixed set of (stored_value,
        display_label) pairs, e.g. compression level "0"/"1"/"2" shown as Low/Medium/High."""
        options = widget_spec["options"]
        value_to_label = dict(options)
        label_to_value = {label: value for value, label in options}
        seg_var = ctk.StringVar(value=value_to_label.get(self.string_vars[key].get(), options[0][1]))

        def _on_segment(selected_label):
            self.string_vars[key].set(label_to_value.get(selected_label, self.string_vars[key].get()))

        ctk.CTkSegmentedButton(row, values=[label for _, label in options], variable=seg_var,
                               command=_on_segment).pack(side="left", padx=5)

    def _build_slider_field(self, row, key, widget_spec):
        """Renders a FIELD_WIDGETS "slider" field: a bounded numeric tuning knob with a live,
        directly-editable readout, still just reading/writing the same string-valued setting."""
        lo, hi = widget_spec["min"], widget_spec["max"]
        step = widget_spec.get("step", 1)
        suffix = widget_spec.get("suffix", "")
        try:
            current_val = float(self.string_vars[key].get())
        except ValueError:
            current_val = lo

        value_var = ctk.StringVar(value=f"{current_val:g}{suffix}")
        value_entry = ctk.CTkEntry(row, textvariable=value_var, width=70, justify="right")
        value_entry.pack(side="right", padx=(5, 0))

        def _apply_typed_value(_event=None):
            raw = value_var.get().strip()
            if suffix and raw.endswith(suffix.strip()):
                raw = raw[:-len(suffix.strip())].strip()
            try:
                typed = float(raw)
            except ValueError:
                value_var.set(f"{slider.get():g}{suffix}")
                return
            rounded = round(max(lo, min(hi, typed)) / step) * step
            slider.set(rounded)
            value_var.set(f"{rounded:g}{suffix}")
            self.string_vars[key].set(f"{rounded:g}")

        value_entry.bind("<Return>", _apply_typed_value)
        value_entry.bind("<FocusOut>", _apply_typed_value)

        def _on_slide(new_val):
            rounded = round(new_val / step) * step
            value_var.set(f"{rounded:g}{suffix}")
            self.string_vars[key].set(f"{rounded:g}")

        steps = max(1, round((hi - lo) / step))
        slider = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=steps, command=_on_slide)
        slider.set(current_val)
        slider.pack(side="left", fill="x", expand=True, padx=5)
        # CTkSlider binds MouseWheel-over-the-slider (on its internal canvas) to change its
        # own value by default - this hijacks scrolling the settings page whenever the
        # cursor merely passes over a slider on the way down. No public API to disable it,
        # so unbind directly.
        canvas = getattr(slider, "_canvas", None)
        if canvas is not None and hasattr(canvas, "unbind"):
            canvas.unbind("<MouseWheel>")

        # Synchronize programatic changes to the underlying string var back to the UI
        def _on_string_var_change(*args):
            try:
                new_val = float(self.string_vars[key].get())
                if abs(slider.get() - new_val) > 1e-5:
                    slider.set(new_val)
                    value_var.set(f"{new_val:g}{suffix}")
            except ValueError:
                pass

        self.string_vars[key].trace_add("write", _on_string_var_change)

    def _resolve_base_dir(self, base_dir_key: str) -> str:
        """Resolves a directory setting (like JSON_DIR) against GENEALOGY_DIR the same way
        execute_script does, so a file browser opens in the same folder the script itself
        will actually look in. The two sentinels resolve to GENEALOGY_DIR's own current value
        and to the toolbox's own code folder, respectively, since neither is itself a
        directory setting nested inside another."""
        prog_dir_var = self.string_vars.get("GENEALOGY_DIR")
        program_dir = prog_dir_var.get().strip() if prog_dir_var is not None else ""
        if base_dir_key == TOOLBOX_DIR_SENTINEL:
            return str(BASE_DIR)
        if base_dir_key == GENEALOGY_DIR_SENTINEL:
            return program_dir or os.getcwd()
        base_var = self.string_vars.get(base_dir_key)
        base_setting = base_var.get().strip() if base_var is not None else ""
        if not base_setting:
            return program_dir or os.getcwd()
        if os.path.isabs(base_setting):
            return base_setting
        return os.path.join(program_dir, base_setting) if program_dir else base_setting

    def _browse_for_path(self, key: str, picker: dict):
        """Opens the dialog matching the field's picker "kind", then stores the result back
        into its StringVar - relative to the field's own base folder when possible (matching
        how these fields are normally typed in), or as a full path for anything picked from
        outside that folder."""
        base_dir = self._resolve_base_dir(picker["base_dir_key"])
        kind = picker.get("kind", "open")
        title = f"Select {self._clean_label(key)}"
        initial_dir = base_dir if os.path.isdir(base_dir) else None

        if kind == "directory":
            selected = filedialog.askdirectory(title=title, initialdir=initial_dir)
        elif kind == "save":
            current_name = os.path.basename(self.string_vars[key].get().strip())
            selected = filedialog.asksaveasfilename(
                title=title, initialdir=initial_dir, initialfile=current_name or None,
                defaultextension=picker.get("defaultextension", ""),
                filetypes=picker.get("filetypes", [("All files", "*.*")]))
        else:
            selected = filedialog.askopenfilename(
                title=title, initialdir=initial_dir,
                filetypes=picker.get("filetypes", [("All files", "*.*")]))

        if not selected:
            return

        selected_path = Path(selected).resolve()
        if picker.get("always_absolute"):
            self.string_vars[key].set(str(selected_path).replace("\\", "/"))
            return

        try:
            rel = selected_path.relative_to(Path(base_dir).resolve())
            self.string_vars[key].set(str(rel).replace("\\", "/"))
        except ValueError:
            self.string_vars[key].set(str(selected_path).replace("\\", "/"))

    def _reset_api_tuning(self):
        defaults = GLOBAL_VARS["API & Processing"]
        for key in ["API_BUDGET", "COST_PER_1M_INPUT", "COST_PER_1M_OUTPUT", "CACHE_DISCOUNT_MULTIPLIER"]:
            if key in self.string_vars:
                self.string_vars[key].set(defaults[key])
        self.console.put("\n[System] API Tuning values reset to defaults. Click 'Save Configuration' to keep them.\n")
        self._expand_console()

    def _build_tab_global(self, parent_frame: ctk.CTkFrame):
        self._build_tab_header(parent_frame, "Global Settings", "SHARED",
                               "The API key, folders, and researcher credit every tool above shares, "
                               "so you only enter them once.")

        # Build buttons first so they dock safely to the bottom
        btn_box = self._create_action_box(parent_frame)
        ctk.CTkButton(btn_box, text="Reset API Tuning Defaults", fg_color=C_SURFACE_RAISED, hover_color=C_BORDER,
                      text_color=C_TEXT,
                      command=self._reset_api_tuning).pack(side="left", padx=5)

        api_keys = {"AI_API_KEY", "EXTRACTION_ENGINE", "MODEL_NAME", "AGY_MODEL_NAME"}
        self._build_form_ui(parent_frame, GLOBAL_VARS, skip_keys=api_keys)

    def _build_tab_archivist(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "Archivist", "BUILD",
                               "Turns gathered or transcribed records into a sourced GEDCOM file, working out "
                               "who belongs to which family from ages, roles, and household position.")

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        ctk.CTkButton(btn_box, text="Generate GEDCOM", fg_color="#2b7a4b", hover_color="#1e5935",
                      text_color=C_TEXT,
                      command=lambda: self.execute_script("ARCHIVIST_SCRIPT", "gedcom_auto")).pack(side="left",
                                                                                                   padx=5)

        self._build_form_ui(frame, ARCHIVIST_VARS)

    def _prompt_search_dirs(self) -> List[Path]:
        """Mirrors Paleographer/engine.py's .pmt search path, highest priority first.
        PROGRAM_DIR itself is never a GUI field (see _run_subprocess) - it's always
        APP_DIR, the app's actual install location, so this reads that directly rather
        than a nonexistent string_vars entry."""
        gen_var = self.string_vars.get("GENEALOGY_DIR")
        gen_dir = gen_var.get().strip() if gen_var else ""
        prompts_var = self.string_vars.get("PROMPTS_DIR")
        prompts_subdir = (prompts_var.get().strip() if prompts_var else "") or "Prompts"
        return prompt_search_dirs(genealogy_dir=gen_dir, prompts_dir=prompts_subdir, program_dir=str(APP_DIR))

    def _list_record_types(self) -> List[str]:
        """Lists every .pmt file found in the multi-tier prompt search path."""
        found = set()
        for d in self._prompt_search_dirs():
            if d.is_dir():
                for p in d.glob("*.pmt"):
                    found.add(p.name)
        if not found:
            return ["Parish.pmt"]
        return sorted(list(found), key=str.lower)

    def _read_pmt_front_matter(self, record_type_value: str) -> dict:
        """Reads a .pmt file's own YAML front matter directly - the same declarative data
        Paleographer.py/Archivist.py themselves read (via engine.parse_type_config /
        apply_record_type_field_remap) to resolve their own settings and vocabulary. The
        GUI shell has no hardcoded knowledge of what a record type means; it only ever
        reads whatever that record type's own file declares. Returns {} for a .pmt that
        doesn't exist or can't be parsed."""
        name = record_type_value.strip() or "Parish.pmt"
        if not name.endswith(".pmt"):
            name += ".pmt"

        for d in self._prompt_search_dirs():
            candidate = d / name
            if candidate.is_file():
                return load_pmt_front_matter(candidate)
        return {}

    def _get_pmt_settings_sections(self, record_type_value: str) -> List[str]:
        """Reads the settings_sections a .pmt's own front matter declares (if any), telling
        the Paleographer tab which PALEOGRAPHER_VARS sections are actually relevant for
        that record type. Undeclared (or unparseable) returns an empty list, meaning "show
        everything" - so older or hand-edited .pmt files don't lose fields by omission."""
        sections = self._read_pmt_front_matter(record_type_value).get("settings_sections")
        return list(sections) if sections else []

    def _get_pmt_field_remap(self, record_type_value: str) -> Dict[str, str]:
        """Reads the field_remap a .pmt's own front matter declares (prefixed settings-tab
        key -> generic runtime name Paleographer.py/Archivist.py each read from their own
        .env - see Parish.pmt/Scrip.pmt). The GUI only ever needs this for its own display
        purposes (e.g. picking the right image-dir field to browse from); the scripts
        resolve it themselves at runtime regardless of whether Antiquarian is involved."""
        return self._read_pmt_front_matter(record_type_value).get("field_remap") or {}

    def _on_record_type_change(self, _value: Optional[str] = None):
        """Rebuilds the settings form to only show the fields the selected .pmt's own
        settings_sections declares as relevant, instead of every Paleographer field for
        every record type. Also gates the Scrip-only enrichment buttons - Enrich Metadata,
        Partition Collections, and Resolve Names have no meaning for non-Scrip record
        types and silently no-op if clicked (classify_sheet_collection has no Scrip-shaped
        fields to key off of), so disable rather than hide them to keep the button row's
        layout stable across Record Type switches."""
        record_type = self.string_vars["PALEOGRAPHER_RECORD_TYPE"].get()

        if hasattr(self, "paleographer_enrich_btn"):
            scrip_state = "normal" if record_type == "Scrip.pmt" else "disabled"
            self.paleographer_enrich_btn.configure(state=scrip_state)
            self.paleographer_partition_btn.configure(state=scrip_state)
            self.paleographer_resolve_names_btn.configure(state=scrip_state)

        if hasattr(self, "paleographer_form_container"):
            for child in self.paleographer_form_container.winfo_children():
                child.destroy()
            sections = self._get_pmt_settings_sections(record_type)
            filtered = {name: fields for name, fields in PALEOGRAPHER_VARS.items()
                        if not sections or name in sections}
            self._build_form_ui(self.paleographer_form_container, filtered,
                                skip_keys={"PALEOGRAPHER_RECORD_TYPE", "PALEOGRAPHER_PDF_COMPRESSION_LEVEL"})

    def _build_tab_paleographer(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "Paleographer", "TRANSCRIBE",
                               "Reads scanned parish registers and scrip records with AI, transcribing and "
                               "translating the handwriting into structured entries.")

        type_frame = ctk.CTkFrame(frame, fg_color="transparent")
        type_frame.pack(side="top", fill="x", pady=(10, 5))
        type_lbl = ctk.CTkLabel(type_frame, text="Record Type ⓘ", font=ctk.CTkFont(weight="bold"), cursor="hand2")
        type_lbl.pack(side="left")
        ToolTip(type_lbl, TOOLTIP_DESCRIPTIONS["PALEOGRAPHER_RECORD_TYPE"])
        ctk.CTkComboBox(type_frame, values=self._list_record_types(),
                        variable=self.string_vars["PALEOGRAPHER_RECORD_TYPE"], width=300,
                        command=self._on_record_type_change).pack(side="left", padx=10)

        debug_frame = ctk.CTkFrame(frame, fg_color="transparent")
        debug_frame.pack(side="top", fill="x", pady=(10, 5))
        ctk.CTkLabel(debug_frame, text="Single Document Extraction (Leave blank for Batch):",
                     font=ctk.CTkFont(weight="bold")).pack(side="left")
        ctk.CTkEntry(debug_frame, textvariable=self.debug_file_var, width=300).pack(side="left", padx=10)
        ctk.CTkButton(debug_frame, text="Browse...", width=90,
                      command=self._browse_debug_file).pack(side="left")

        compress_frame = ctk.CTkFrame(frame, fg_color="transparent")
        compress_frame.pack(side="top", fill="x", pady=(10, 5))
        comp_lbl = ctk.CTkLabel(compress_frame, text="PDF Compression Level ⓘ", font=ctk.CTkFont(weight="bold"),
                                cursor="hand2")
        comp_lbl.pack(side="left")
        ToolTip(comp_lbl, TOOLTIP_DESCRIPTIONS.get("PALEOGRAPHER_PDF_COMPRESSION_LEVEL", ""))
        self._build_segmented_field(compress_frame, "PALEOGRAPHER_PDF_COMPRESSION_LEVEL",
                                    FIELD_WIDGETS["PALEOGRAPHER_PDF_COMPRESSION_LEVEL"])

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        ctk.CTkButton(btn_box, text="Run Analysis (API)", fg_color="#3B8ED0", hover_color="#2b7a4b",
                      text_color=C_TEXT,
                      command=lambda: self.execute_script("ANALYSIS_SCRIPT", "paleographer_api")
                      ).pack(side="left", padx=5)
        self.paleographer_enrich_btn = ctk.CTkButton(
            btn_box, text="Enrich Metadata", fg_color="#2b7a4b", hover_color="#1e5935",
            text_color=C_TEXT, command=lambda: self.execute_script("ANALYSIS_SCRIPT", "enrich"))
        self.paleographer_enrich_btn.pack(side="left", padx=5)
        self.paleographer_partition_btn = ctk.CTkButton(
            btn_box, text="Partition Collections", fg_color="#7A5B2B", hover_color="#5B431E",
            text_color=C_TEXT, command=lambda: self.execute_script("ANALYSIS_SCRIPT", "partition"))
        self.paleographer_partition_btn.pack(side="left", padx=5)
        self.paleographer_resolve_names_btn = ctk.CTkButton(
            btn_box, text="Resolve Names", fg_color="#4A5568", hover_color="#2D3748",
            text_color=C_TEXT, command=lambda: self.execute_script("ANALYSIS_SCRIPT", "resolve-names"))
        self.paleographer_resolve_names_btn.pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="Clear Cache", fg_color="#991b1b", hover_color="#7f1d1d",
                      text_color=C_TEXT,
                      command=lambda: self.execute_script("CLEANUP_CACHE_SCRIPT", "standalone")).pack(side="right",
                                                                                                      padx=5)

        # Clear fields button above the form container
        clear_frame = ctk.CTkFrame(frame, fg_color="transparent")
        clear_frame.pack(side="top", fill="x", pady=(5, 0))
        ctk.CTkButton(clear_frame, text="Clear Overrides & Settings", fg_color=C_SURFACE_RAISED, hover_color=C_BORDER,
                      text_color=C_TEXT, command=self._clear_paleographer_overrides).pack(side="left")

        # Persistent container the filtered settings form gets rebuilt into whenever the
        # Record Type changes, so switching types only shows the fields that type uses.
        self.paleographer_form_container = ctk.CTkFrame(frame, fg_color="transparent")
        self.paleographer_form_container.pack(side="top", fill="both", expand=True)

        self._on_record_type_change()

    def _clear_paleographer_overrides(self):
        """Clears all text inputs in the dynamic Parish/Citation/Scrip sections."""
        for section in ["Parish Information", "Citation Overrides", "Scrip Information"]:
            for key in PALEOGRAPHER_VARS.get(section, {}).keys():
                if key in self.string_vars:
                    self.string_vars[key].set("")

    def _browse_debug_file(self):
        """Opens a file browser rooted in whichever image folder the current Record Type
        actually reads from (nested under MEDIA_DIR - mirroring how each script resolves
        its own IMAGE_DIR), storing just the bare filename since that's what Paleographer.py
        compares DEBUG_FILE against."""
        record_type = self.string_vars["PALEOGRAPHER_RECORD_TYPE"].get()
        media_base = self._resolve_base_dir("MEDIA_DIR")
        source_dir = os.path.join(media_base, record_type.replace(".pmt", ""))

        selected = filedialog.askopenfilename(
            title="Select Debug Image File", initialdir=source_dir if os.path.isdir(source_dir) else None,
            filetypes=[("Image/PDF files", "*.jpg *.jpeg *.png *.tif *.tiff *.pdf"), ("All files", "*.*")])
        if not selected:
            return
        self.debug_file_var.set(os.path.basename(selected))

    def _launch_lac_debug_browser(self):
        """Launches a dedicated Chrome/Edge window with --remote-debugging-port
        active (fresh profile under Working/LAC/, never touching your normal
        browsing profile) to obtain LAC session cookies via CDP."""
        program_dir_var = self.string_vars.get("GENEALOGY_DIR")
        program_dir = program_dir_var.get().strip() if program_dir_var is not None else ""
        profile_dir = os.path.join(program_dir or os.getcwd(), "Working", "LAC", "DebugBrowserProfile")
        os.makedirs(profile_dir, exist_ok=True)

        candidates = [
            shutil.which("chrome"), shutil.which("msedge"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        browser_path = next((p for p in candidates if p and os.path.isfile(p)), None)
        if not browser_path:
            self.console.put("\n[System] Could not find Chrome or Edge in a standard location. Launch one "
                             "manually with --remote-debugging-port=9222 and a separate --user-data-dir instead.\n")
            return

        subprocess.Popen([browser_path, "--remote-debugging-port=9222", f"--user-data-dir={profile_dir}",
                          "--remote-allow-origins=*",
                          "https://recherche-collection-search.bac-lac.gc.ca/eng/Home/SearchAdvanced"])
        self.console.put("\n[System] Launched a debuggable browser window (port 9222). Search LAC in "
                         "that window to establish Cloudflare clearance.\n")

    def _open_lac_cookie_file(self):
        """Opens the LAC cookie fallback file in the default text editor."""
        cookie_path = Path(self._resolve_base_dir("LAC_COOKIE_FILE"))
        cookie_path.parent.mkdir(parents=True, exist_ok=True)
        if not cookie_path.is_file():
            cookie_path.write_text("", encoding="utf-8")
        if sys.platform == "win32":
            os.startfile(str(cookie_path))
        elif sys.platform == "darwin":
            subprocess.call(["open", str(cookie_path)])
        else:
            subprocess.call(["xdg-open", str(cookie_path)])

    @staticmethod
    def _voyageur_code_for_label(label: str) -> str:
        return next((c for c, lbl in VOYAGEUR_SOURCES if lbl == label), VOYAGEUR_SOURCES[0][0])

    @staticmethod
    def _voyageur_visible_sections(label: str) -> Dict[str, Dict[str, str]]:
        """Fields shown for the given VOYAGEUR_SOURCE label: the universal "Gather Settings"
        section (shown for all sources, including GATHER_ON_COLLISION which every Voyageur
        sub-script now reads) plus the provider's own section if it has one."""
        result = {}

        if "Gather Settings" in VOYAGEUR_VARS:
            result["Gather Settings"] = dict(VOYAGEUR_VARS["Gather Settings"])

        section_name = "HBCA Settings" if label == "Keystone Archives" else label
        if section_name in VOYAGEUR_VARS:
            result[section_name] = VOYAGEUR_VARS[section_name]

        return result

    def _on_voyageur_source_change(self, _value: Optional[str] = None):
        """Rebuilds the settings form and gather button to match the selected repository -
        each source shows its own settings section (mirroring how Paleographer's Record
        Type dropdown filters its own form down to one .pmt's declared sections) plus the
        always-shown "Gather Settings" section - see _voyageur_visible_sections."""
        label = self.string_vars["VOYAGEUR_SOURCE"].get() or VOYAGEUR_SOURCES[0][1]
        code = self._voyageur_code_for_label(label)

        if hasattr(self, "voyageur_gather_btn"):
            self.voyageur_gather_btn.configure(text=f"Gather from {label}",
                                               command=lambda: self.execute_script("VOYAGEUR_SCRIPT", code))

        if hasattr(self, "voyageur_gather_build_btn"):
            # Chains straight into Generate GEDCOM (the same "gedcom_auto" mode the
            # Archivist tab's own button uses) only once the gather actually finishes
            # cleanly - see execute_script's on_success.
            self.voyageur_gather_build_btn.configure(
                command=lambda: self.execute_script(
                    "VOYAGEUR_SCRIPT", code,
                    on_success=lambda: self.execute_script("ARCHIVIST_SCRIPT", "gedcom_auto")))

        if hasattr(self, "voyageur_gather_transcribe_build_btn"):
            # Chains Voyageur -> Paleographer (API mode) -> Archivist (gedcom_auto)
            self.voyageur_gather_transcribe_build_btn.configure(
                command=lambda: self.execute_script(
                    "VOYAGEUR_SCRIPT", code,
                    on_success=lambda: self.execute_script(
                        "ANALYSIS_SCRIPT", "paleographer_api",
                        on_success=lambda: self.execute_script("ARCHIVIST_SCRIPT", "gedcom_auto")
                    )
                )
            )

        if hasattr(self, "voyageur_form_container"):
            for child in self.voyageur_form_container.winfo_children():
                child.destroy()
            filtered = self._voyageur_visible_sections(label)
            self._build_form_ui(self.voyageur_form_container, filtered, skip_keys={"VOYAGEUR_SOURCE"})

    def _build_tab_voyageur(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "Voyageur", "GATHER",
                               "Pulls census, vital, and land records straight from Ancestry, FamilySearch, or "
                               "Library and Archives Canada into a file ready for the next step.")

        source_frame = ctk.CTkFrame(frame, fg_color="transparent")
        source_frame.pack(side="top", fill="x", pady=(10, 5))
        source_lbl = ctk.CTkLabel(source_frame, text="Gather From ⓘ", font=ctk.CTkFont(weight="bold"),
                                  cursor="hand2")
        source_lbl.pack(side="left")
        ToolTip(source_lbl, TOOLTIP_DESCRIPTIONS["VOYAGEUR_SOURCE"])
        if not self.string_vars["VOYAGEUR_SOURCE"].get():
            self.string_vars["VOYAGEUR_SOURCE"].set(VOYAGEUR_SOURCES[0][1])
        ctk.CTkComboBox(source_frame, values=[label for _, label in VOYAGEUR_SOURCES],
                        variable=self.string_vars["VOYAGEUR_SOURCE"], width=200,
                        command=self._on_voyageur_source_change).pack(side="left", padx=10)

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        self.voyageur_gather_btn = ctk.CTkButton(btn_box, text="Gather", fg_color="#3B8ED0", hover_color="#2b7a4b",
                                                 text_color=C_TEXT)
        self.voyageur_gather_btn.pack(side="left", padx=5)

        self.voyageur_gather_build_btn = ctk.CTkButton(
            btn_box, text="Gather & Build", fg_color="#2b7a4b", hover_color="#1e5935",
            text_color=C_TEXT)
        self.voyageur_gather_build_btn.pack(side="left", padx=5)

        self.voyageur_gather_transcribe_build_btn = ctk.CTkButton(
            btn_box, text="Gather, Transcribe & Build", fg_color="#7A5B2B", hover_color="#5B431E",
            text_color=C_TEXT)
        self.voyageur_gather_transcribe_build_btn.pack(side="left", padx=5)

        # Persistent container the filtered settings form gets rebuilt into whenever the
        # source changes, so switching repositories only shows the fields that source uses.
        self.voyageur_form_container = ctk.CTkFrame(frame, fg_color="transparent")
        self.voyageur_form_container.pack(side="top", fill="both", expand=True)

        self._on_voyageur_source_change()

    def _build_tab_registrar(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "Registrar", "DEDUPE",
                               "Finds logical duplicate people in RootsMagic using smart name and age matching, "
                               "and flags them for manual review.")

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        ctk.CTkButton(btn_box, text="Run Script", fg_color="#2b7a4b", hover_color="#1e5935", text_color=C_TEXT,
                      command=lambda: self.execute_script("REGISTRAR_SCRIPT", "standalone")).pack(side="left", padx=5)
        ctk.CTkButton(btn_box, text="Reset Defaults", fg_color="#555555", hover_color="#333333", text_color=C_TEXT,
                      command=self._reset_registrar_defaults).pack(side="left", padx=5)

        self._build_form_ui(frame, REGISTRAR_VARS)

    def _reset_registrar_defaults(self):
        """Restores the fine-tuning thresholds back to recommended defaults."""
        defaults = {
            "REGISTRAR_FUZZY_THRESHOLD": "82",
            "REGISTRAR_MAX_AGE_GAP": "5",
            "REGISTRAR_FUZZY_THRESHOLD_STRICT": "95",
            "REGISTRAR_FAMILY_MATCH_THRESHOLD": "75"
        }
        for key, val in defaults.items():
            if key in self.string_vars:
                self.string_vars[key].set(val)
        self.console.put("\n[System] Registrar thresholds reset to defaults. "
                         "Click 'Save Configuration' to keep them.\n")
        self._expand_console()

    def _build_tab_gazetteer(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "Gazetteer", "UTILITY",
                               "Corrects County or Territory place names in RootsMagic to match historical "
                               "boundaries for the exact year of each event.")

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        ctk.CTkButton(btn_box, text="Run Script", fg_color="#2b7a4b", hover_color="#1e5935", text_color=C_TEXT,
                      command=lambda: self.execute_script("GAZETTEER_SCRIPT", "standalone")).pack(side="left", padx=5)

        self._build_form_ui(frame, GAZETTEER_VARS)

    def _build_tab_pdfix(self, frame: ctk.CTkFrame):
        self._build_tab_header(frame, "PDFix", "UTILITY",
                               "Losslessly shrinks PDF file sizes in bulk (garbage-collection + stream "
                               "compression) - no image rescaling.")

        # Unified action buttons (Docked to bottom)
        btn_box = self._create_action_box(frame)
        ctk.CTkButton(btn_box, text="Run Script", fg_color="#2b7a4b", hover_color="#1e5935", text_color=C_TEXT,
                      command=lambda: self.execute_script("PDFIX_SCRIPT", "standalone")).pack(side="left", padx=5)

        self._build_form_ui(frame, PDFIX_VARS)

    def execute_script(self, script_key, mode, on_success=None):
        """Prepares environment variables and launches an external script in a background
        thread. If on_success is given, it's called once the subprocess exits with code 0 -
        letting a caller chain a follow-up run (e.g. "Gather and Send to Archivist" auto-running
        Generate GEDCOM once a gather finishes cleanly), without chaining onto a cancelled
        or failed run."""
        self._save_env()

        script_path_str = SCRIPT_PATHS.get(script_key)
        if not script_path_str:
            self.console.put(f"\n[System] Unknown script key: '{script_key}'.\n")
            return

        target_script_path = os.path.abspath(os.path.join(str(BASE_DIR), script_path_str))

        if not os.path.exists(target_script_path):
            self.console.put(f"\n[System] Could not find the script at: {target_script_path}\n")
            return

        script_display_name = os.path.basename(target_script_path)
        self.status_bar.configure(state="normal")
        self.status_bar.delete(0, "end")
        self.status_bar.insert(0, f"Launching {script_display_name}...")
        self.status_bar.configure(state="readonly")
        self.run_indicator.configure(text_color=C_ACCENT)
        self.run_tooltip.text = f"Running: {script_display_name}"
        self._expand_console()

        run_env = os.environ.copy()
        run_env.update({k: str(v.get()) for k, v in self.string_vars.items()})

        # --- DYNAMIC PATH RESOLUTION ---
        def resolve_path(base, sub):
            if not sub or sub == ".":
                return base
            if os.path.isabs(sub):
                return sub
            return os.path.normpath(os.path.join(base, sub)).replace("\\", "/")

        prog_dir_var: Union[ctk.StringVar, None] = self.string_vars.get("GENEALOGY_DIR")
        program_dir = prog_dir_var.get().strip() if prog_dir_var is not None else ""
        media_dir_var = self.string_vars.get("MEDIA_DIR")
        media_base = media_dir_var.get().strip() if media_dir_var is not None else "Media"
        rm_dir_var = self.string_vars.get("RM_DIR")
        rm_base = rm_dir_var.get().strip() if rm_dir_var is not None else "Roots Magic 11"

        full_media_dir = resolve_path(program_dir, media_base)
        full_rm_dir = resolve_path(program_dir, rm_base)

        env_overrides = {}

        # Pre-resolve these specific nested directory variables
        nested_dir_keys = [("REGISTRAR_RM_DATABASE", full_rm_dir), ("GAZETTEER_RM_DATABASE", full_rm_dir),
                           ("PDFIX_TARGET_DIR", full_media_dir)]
        for key, base_dir in nested_dir_keys:
            if key in self.string_vars:
                env_overrides[key] = resolve_path(base_dir, self.string_vars[key].get())

        # Inject runtime overrides. Downstream modules (Paleographer/Archivist)
        # independently resolve their own schemas via .pmt field_remaps, so we
        # avoid computing record-type-specific logic here.
        run_env.update(env_overrides)

        self._set_ui_state("disabled")

        def on_complete(status_msg="System Ready"):
            # Shows the outcome of the run that just finished (success/failed/cancelled and
            # which script), not a generic "System Ready" that erases that information the
            # moment the run ends - stays until the next run overwrites it.
            self._set_ui_state("normal")
            self.status_bar.configure(state="normal")
            self.status_bar.delete(0, "end")
            self.status_bar.insert(0, status_msg)
            self.status_bar.configure(state="readonly")
            self.run_indicator.configure(text_color=C_TEXT_MUTED)
            self.run_tooltip.text = "Idle - no script running"
            self.progress_bar.set(0)
            self.progress_pct_label.configure(text="0%")
            self.progress_row.grid_remove()

        args = [target_script_path]
        arg_builder = ARG_SPECS.get((script_key, mode))
        if arg_builder:
            args.extend(arg_builder(self.string_vars, self.debug_file_var))

        target_cwd = os.path.dirname(target_script_path) if os.path.exists(target_script_path) else None

        threading.Thread(target=self._run_subprocess, args=(args, run_env, target_cwd, on_complete, on_success),
                         daemon=True).start()

    def _run_subprocess(self, safe_cmd, run_env, target_cwd, on_complete, on_success=None):
        run_env['PYTHONUNBUFFERED'] = '1'
        run_env['PYTHONIOENCODING'] = 'utf-8'
        # The app's own install location - not a GUI-editable setting, so it never rides
        # along via string_vars like the rest of run_env. Tools that resolve fixed,
        # ships-with-the-app paths (e.g. Gazetteer's shapefiles, Paleographer's Prompts
        # fallback) read this rather than a settings key. Must be APP_DIR, not BASE_DIR:
        # BASE_DIR is __file__-relative, which lands inside PyInstaller's _internal bundle
        # dir when frozen (see APP_DIR's own definition above) - a portable install's
        # bundled Prompts folder lives next to the .exe, not in there, so PROGRAM_DIR
        # pointed at BASE_DIR silently couldn't find it.
        run_env.setdefault('PROGRAM_DIR', str(APP_DIR))

        script_path = safe_cmd[0]
        try:
            rel_path = Path(script_path).relative_to(BASE_DIR)
            module_name = str(rel_path).replace("\\", ".").replace("/", ".").replace(".py", "")
        except ValueError:
            module_name = script_path

        new_cmd = [sys.executable, __file__, "--module", module_name] + safe_cmd[1:]

        script_name = os.path.basename(script_path)
        self.console.put(f"\n[System] Starting {script_name}...\n")
        self.console.put("-" * 50 + "\n")

        self._cancel_requested = False
        succeeded = False
        try:
            self.active_process = subprocess.Popen(new_cmd, stdin=subprocess.PIPE,
                                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                                                   env=run_env, cwd=target_cwd)

            stdout_stream = io.TextIOWrapper(self.active_process.stdout, encoding='utf-8', newline='', errors='replace')
            try:
                while True:
                    chunk = stdout_stream.read(256)
                    if not chunk:
                        break
                    self.console.put(chunk)
                self.active_process.wait()
            finally:
                if self.active_process and self.active_process.poll() is None:
                    self.active_process.terminate()
                    self.active_process.wait(timeout=2)

            if self.active_process.returncode == 0:
                self.console.put(f"\n[System] {script_name} finished successfully!\n")
                succeeded = True
                status_msg = f"{script_name}: finished successfully"
            elif self._cancel_requested:
                # terminate() reports the same exit code (1 on Windows) as a genuine
                # unhandled exception, so cancellation can only be told apart from a real
                # crash via this flag, not the returncode itself.
                self.console.put("\n[System] Task was cancelled by you.\n")
                status_msg = f"{script_name}: cancelled"
            else:
                self.console.put(
                    f"\n[System] {script_name} encountered an error (exit code "
                    f"{self.active_process.returncode}). Please check the text above for clues.\n")
                status_msg = f"{script_name}: failed (exit code {self.active_process.returncode})"

        except (OSError, subprocess.SubprocessError, ValueError) as e:
            self.console.put(f"\n[System] Failed to execute {script_name}:\n{str(e)}\n")
            status_msg = f"{script_name}: failed to start"

        self.active_process = None
        # on_complete's own delay (100ms) fires first, re-enabling the UI, before
        # on_success (150ms) potentially launches a follow-up script through
        # execute_script - which disables the UI again itself - so the two calls'
        # UI-state changes can't race each other out of order.
        self.after(100, partial(on_complete, status_msg))
        if succeeded and on_success:
            self.after(150, on_success)

    def _set_ui_state(self, state):
        # Deliberately only content_area, not self.sidebar - tab switching should stay
        # possible while a script runs in the background.
        self._recursive_state(self.content_area, state)

        if hasattr(self, 'console_input'):
            if state == "disabled":
                self.console_input.configure(state="normal")
                self.cancel_btn.configure(state="normal")
            else:
                self.console_input.configure(state="disabled")
                self.cancel_btn.configure(state="disabled")

    def _recursive_state(self, widget, state):
        if isinstance(widget, ctk.CTkButton):
            widget.configure(state=state)
        for child in widget.winfo_children():
            self._recursive_state(child, state)


if __name__ == "__main__":
    import argparse
    import runpy

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--module", type=str)
    args, unknown = parser.parse_known_args()

    if args.module:
        sys.argv = [args.module] + unknown
        runpy.run_module(args.module, run_name="__main__")
        sys.exit(0)

    app = Antiquarian()
    app.mainloop()
