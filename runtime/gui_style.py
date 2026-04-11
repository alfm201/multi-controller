"""Shared lightweight ttk styling for the runtime GUI."""

from __future__ import annotations

PALETTE = {
    "app_bg": "#f5f7fb",
    "surface": "#ffffff",
    "surface_alt": "#eef2f7",
    "canvas": "#fbfcfe",
    "border": "#d7dee8",
    "text": "#0f172a",
    "muted": "#5b6678",
    "success_bg": "#e8f8ef",
    "success_fg": "#166534",
    "warning_bg": "#fff4db",
    "warning_fg": "#9a6700",
    "danger_bg": "#fee7e7",
    "danger_fg": "#b42318",
    "accent_bg": "#e4f0ff",
    "accent_fg": "#1d4ed8",
    "neutral_bg": "#edf2f7",
    "neutral_fg": "#475569",
    "toggle_on": "#dbeafe",
    "toggle_on_text": "#1d4ed8",
    "toggle_off": "#eef2f7",
    "toggle_off_text": "#334155",
    "tab_selected": "#ffffff",
    "tab_idle": "#e9eef5",
}


def palette_for_tone(tone: str) -> tuple[str, str]:
    mapping = {
        "success": (PALETTE["success_bg"], PALETTE["success_fg"]),
        "warning": (PALETTE["warning_bg"], PALETTE["warning_fg"]),
        "danger": (PALETTE["danger_bg"], PALETTE["danger_fg"]),
        "accent": (PALETTE["accent_bg"], PALETTE["accent_fg"]),
        "neutral": (PALETTE["neutral_bg"], PALETTE["neutral_fg"]),
    }
    return mapping.get(tone, mapping["neutral"])


def apply_gui_theme(root) -> None:
    """Apply a small, low-cost ttk style layer."""
    from tkinter import ttk

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    root.configure(bg=PALETTE["app_bg"])

    style.configure(".", background=PALETTE["app_bg"], foreground=PALETTE["text"])
    style.configure("TFrame", background=PALETTE["app_bg"])
    style.configure("App.TFrame", background=PALETTE["app_bg"])
    style.configure("Surface.TFrame", background=PALETTE["surface"])
    style.configure("Toolbar.TFrame", background=PALETTE["app_bg"])
    style.configure(
        "Panel.TLabelframe",
        background=PALETTE["surface"],
        borderwidth=1,
        relief="solid",
    )
    style.configure(
        "Panel.TLabelframe.Label",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
    )
    style.configure("TLabel", background=PALETTE["app_bg"], foreground=PALETTE["text"])
    style.configure("Surface.TLabel", background=PALETTE["surface"], foreground=PALETTE["text"])
    style.configure("Muted.TLabel", background=PALETTE["app_bg"], foreground=PALETTE["muted"])
    style.configure(
        "SurfaceMuted.TLabel",
        background=PALETTE["surface"],
        foreground=PALETTE["muted"],
    )
    style.configure(
        "Heading.TLabel",
        background=PALETTE["app_bg"],
        foreground=PALETTE["text"],
        font=("", 11, "bold"),
    )
    style.configure(
        "InspectorTitle.TLabel",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        font=("", 12, "bold"),
    )

    style.configure(
        "TNotebook",
        background=PALETTE["app_bg"],
        borderwidth=0,
        tabmargins=(0, 0, 0, 0),
    )
    style.configure(
        "TNotebook.Tab",
        padding=(10, 6),
        background=PALETTE["tab_idle"],
        foreground=PALETTE["muted"],
        borderwidth=0,
        font=("", 9),
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PALETTE["tab_selected"])],
        foreground=[("selected", PALETTE["text"])],
        padding=[("selected", (16, 10))],
        font=[("selected", ("", 11, "bold"))],
    )

    style.configure(
        "TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        borderwidth=1,
        relief="solid",
        padding=(10, 6),
    )
    style.map(
        "TButton",
        background=[("active", PALETTE["surface_alt"]), ("disabled", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["muted"])],
    )
    style.configure("Primary.TButton", background=PALETTE["accent_bg"], foreground=PALETTE["accent_fg"])
    style.map(
        "Primary.TButton",
        background=[("active", "#d7e8ff"), ("disabled", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["muted"])],
    )
    style.configure(
        "Toolbar.TButton",
        background=PALETTE["surface"],
        foreground=PALETTE["text"],
        padding=(12, 8),
        font=("", 10),
    )
    style.map(
        "Toolbar.TButton",
        background=[("active", PALETTE["surface_alt"]), ("disabled", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["muted"])],
    )
    style.configure(
        "ToggleOn.TButton",
        background=PALETTE["toggle_on"],
        foreground=PALETTE["toggle_on_text"],
        padding=(12, 8),
        font=("", 10),
    )
    style.map(
        "ToggleOn.TButton",
        background=[("active", PALETTE["toggle_on"]), ("disabled", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["muted"])],
    )
    style.configure(
        "ToggleOff.TButton",
        background=PALETTE["toggle_off"],
        foreground=PALETTE["toggle_off_text"],
        padding=(12, 8),
        font=("", 10),
    )
    style.map(
        "ToggleOff.TButton",
        background=[("active", PALETTE["toggle_off"]), ("disabled", PALETTE["surface_alt"])],
        foreground=[("disabled", PALETTE["muted"])],
    )

    style.configure(
        "Treeview",
        background=PALETTE["surface"],
        fieldbackground=PALETTE["surface"],
        foreground=PALETTE["text"],
        rowheight=28,
        borderwidth=0,
    )
    style.configure(
        "Treeview.Heading",
        background=PALETTE["surface_alt"],
        foreground=PALETTE["text"],
        relief="flat",
    )
