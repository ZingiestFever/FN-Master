"""
ui_theme.py — оформление FN Master (градиентная тема, версия 3).
Только внешний вид; механика остаётся в fn_master.py.
"""
import ctypes
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

ASSETS = Path(__file__).resolve().parent / "assets"

BG = "#0F1230"
PANEL = "#161C3A"
BORDER = "#2B3667"
TERM = "#0A0E1F"
MUTED = "#9AA6CF"
ACCENT = "#38BDF8"
GRADIENT = ["#2A1B5E", "#131A3F", "#0B2A3A"]

CARD = {
    "ok":      ("#10302A", "#22C55E", "#86EFAC"),
    "warn":    ("#33290C", "#EAB308", "#FDE68A"),
    "err":     ("#3D1420", "#EF4444", "#FCA5A5"),
    "neutral": ("#1C2448", "#3B4A7A", "#E2E8F0"),
}


def _mix(widget, c1, c2, t):
    a, b = widget.winfo_rgb(c1), widget.winfo_rgb(c2)
    return "#%02x%02x%02x" % tuple(
        int((a[i] + (b[i] - a[i]) * t) / 256) for i in range(3)
    )


def _ramp(widget, stops, t):
    t = t * (len(stops) - 1)
    k = min(int(t), len(stops) - 2)
    return _mix(widget, stops[k], stops[k + 1], t - k)


def _app_id(app):
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "DetMir.FNMaster"
    )


def _icon(app):
    images = [
        tk.PhotoImage(file=str(ASSETS / name))
        for name in ("icon_64.png", "icon_32.png")
        if (ASSETS / name).is_file()
    ]
    if images:
        app._theme_icons = images
        app.iconphoto(True, *images)


def _backdrop(app):
    """Градиентный фон всего окна."""
    app.configure(bg=BG)
    cv = tk.Canvas(app, highlightthickness=0, bd=0, bg=BG)
    cv.place(x=0, y=0, relwidth=1, relheight=1)
    tk.Misc.lower(cv)

    def draw(event=None):
        cv.delete("all")
        w, h, n = cv.winfo_width(), cv.winfo_height(), 60
        for i in range(n):
            cv.create_rectangle(
                0, h * i / n, w, h * (i + 1) / n + 1,
                fill=_ramp(cv, GRADIENT, i / (n - 1)), width=0
            )

    cv.bind("<Configure>", draw)


def _panel(frame, bg, fg):
    frame.config(bg=bg, highlightbackground=BORDER, highlightthickness=1)
    frame.pack_configure(padx=18)
    for child in frame.winfo_children():
        cls = child.winfo_class()
        if cls == "Label":
            child.config(bg=bg, fg=fg)
        elif cls == "Frame":
            child.config(bg=bg)


def _outline(btn, bg):
    """Контурная кнопка: заливается цветом при наведении."""
    color = btn["bg"]
    btn.config(
        bg=_mix(btn, bg, color, 0.16), fg=_mix(btn, color, "#FFFFFF", 0.55),
        activebackground=color, activeforeground="white",
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground=color, highlightcolor=color,
        disabledforeground="#56628A", cursor="hand2"
    )
    btn._t_hover = (color, "white")


def _keycap(btn):
    """Главная кнопка: сплошной цвет и тёмная нижняя грань."""
    color = btn["bg"]
    light = _mix(btn, color, "#FFFFFF", 0.18)
    shadow = _mix(btn, color, "#000000", 0.45)
    btn.config(
        font=("Segoe UI", 12, "bold"), pady=14,
        activebackground=light, activeforeground="white",
        relief="flat", bd=0, highlightthickness=0,
        disabledforeground="#CBD5E1", cursor="hand2"
    )
    btn._t_hover = (light, "white")

    info = btn.grid_info()
    btn.grid_configure(pady=(0, 0))
    tk.Frame(btn.master, bg=shadow, height=5).grid(
        row=int(info.get("row", 0)) + 1, column=int(info["column"]),
        sticky="ew", padx=5
    )


def _top(app):
    bar = app.connect_btn.master
    title = next(
        (c for c in bar.winfo_children()
         if c.winfo_class() == "Label" and c["text"] == "FN MASTER"),
        None
    )
    path = ASSETS / "logo.png"
    if title is not None and path.is_file():
        app._theme_logo = tk.PhotoImage(file=str(path))
        tk.Label(bar, image=app._theme_logo, bg=bar["bg"], bd=0).pack(
            side="left", padx=(0, 10), before=title
        )

    line = tk.Canvas(app, height=3, bg=BG, highlightthickness=0)
    line.pack(side="top", fill="x", after=bar)
    stops = ["#A855F7", "#38BDF8", "#22C55E"]

    def draw(event=None):
        line.delete("all")
        w, n = max(line.winfo_width(), 1), 80
        for i in range(n):
            line.create_rectangle(
                w * i / n, 0, w * (i + 1) / n + 1, 3,
                fill=_ramp(line, stops, i / (n - 1)), width=0
            )

    line.bind("<Configure>", draw)


def _cards(app):
    def set_card(key, text_val, status="neutral"):
        if key not in app.cards:
            return
        cell, hdr_row, lbl_t, lbl_v = app.cards[key]
        bg, border, fg = CARD.get(status, CARD["neutral"])
        cell.config(bg=bg, highlightbackground=border)
        hdr_row.config(bg=bg)
        lbl_t.config(bg=bg, fg=MUTED)
        lbl_v.config(bg=bg, fg=fg, text=text_val)

    app.set_card = set_card

    wrap = next(iter(app.cards.values()))[0].master
    _panel(wrap, PANEL, MUTED)
    for key, (cell, hdr_row, lbl_t, lbl_v) in app.cards.items():
        cell.config(padx=12, pady=7)
        lbl_v.config(font=("Segoe UI", 10, "bold"))
        set_card(key, lbl_v["text"], "neutral")

    app.edit_req_btn.config(bg="#2563EB")
    _outline(app.edit_req_btn, CARD["neutral"][0])


def _panels(app):
    service = app.action_buttons[0].master
    _panel(service, PANEL, MUTED)
    for btn in app.action_buttons[:-3]:
        btn.config(pady=6)
        _outline(btn, PANEL)

    main_ops = app.action_buttons[-1].master.master
    _panel(main_ops, PANEL, "#E2E8F0")
    for btn in app.action_buttons[-3:]:
        _keycap(btn)

    bottom = app.progress.master
    bottom.config(bg="#0A0D22", highlightbackground=BORDER)
    for child in bottom.winfo_children():
        if child.winfo_class() == "Label":
            child.config(bg="#0A0D22", fg=MUTED)
    tk.Label(
        bottom, text=f"v{getattr(app, 'app_version', '')}",
        font=("Consolas", 9), bg="#0A0D22", fg="#56628A"
    ).pack(side="right", padx=(8, 0))


def _terminal(app):
    box = app.text
    frame = box.master
    frame.config(bg=TERM, highlightbackground=BORDER)
    frame.pack_configure(padx=18, pady=(2, 12))

    mono = "Cascadia Mono" if "Cascadia Mono" in tkfont.families() else "Consolas"
    box.config(
        bg=TERM, fg="#CBD5E1", font=(mono, 10), padx=12, pady=8,
        spacing1=2, spacing3=2, insertbackground=TERM,
        selectbackground="#4338CA", bd=0, highlightthickness=0
    )
    bold = (mono, 10, "bold")
    box.tag_config("INFO", foreground="#CBD5E1", font=(mono, 10))
    box.tag_config("SUCCESS", foreground="#4ADE80", font=bold)
    box.tag_config("WARN", foreground="#FACC15", font=bold)
    box.tag_config("ERROR", foreground="#F87171", background="#2A1220", font=bold)
    box.tag_config("HEADER", foreground="#A5B4FC", background="#1A2152", font=bold)
    box.tag_raise("sel")

    header = next(
        (c for c in frame.winfo_children() if c.winfo_class() == "Frame"),
        None
    )
    if header is None:
        return
    header.config(bg="#1A2152")
    for child in header.winfo_children():
        cls = child.winfo_class()
        if cls == "Label":
            child.config(bg="#1A2152", fg="#C7D2FE")
        elif cls == "Button":
            _outline(child, "#1A2152")


def _ttk(app):
    style = ttk.Style(app)
    style.theme_use("clam")
    style.configure(
        "TCombobox", padding=4, fieldbackground="white", background="white",
        bordercolor=BORDER, arrowcolor="#0F172A"
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", "white"), ("disabled", "#CBD5E1")],
        foreground=[("readonly", "#0F172A")],
        selectbackground=[("readonly", "white")],
        selectforeground=[("readonly", "#0F172A")]
    )
    style.configure("TEntry", padding=4, fieldbackground="white")
    style.configure(
        "Horizontal.TProgressbar", thickness=8, troughcolor=BORDER,
        background=ACCENT, bordercolor=BORDER,
        lightcolor=ACCENT, darkcolor=ACCENT
    )

    # Тонкая полоса прокрутки без стрелок.
    try:
        style.layout("Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {
                "sticky": "ns",
                "children": [
                    ("Vertical.Scrollbar.thumb",
                     {"expand": "1", "sticky": "nswe"})
                ]
            })
        ])
    except tk.TclError:
        pass
    style.configure(
        "Vertical.TScrollbar", troughcolor=TERM, background="#3B4A7A",
        bordercolor=TERM, lightcolor="#3B4A7A", darkcolor="#3B4A7A",
        gripcount=0, arrowsize=12, relief="flat"
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", "#56628A"), ("disabled", TERM)],
        lightcolor=[("active", "#56628A"), ("disabled", TERM)],
        darkcolor=[("active", "#56628A"), ("disabled", TERM)]
    )


def _hover(app):
    def enter(event):
        try:
            w = event.widget
            if str(w["state"]) == "disabled" or hasattr(w, "_t_saved"):
                return
            w._t_saved = (w["bg"], w["fg"])
            if hasattr(w, "_t_hover"):
                w.config(bg=w._t_hover[0], fg=w._t_hover[1])
            else:
                dark = sum(w.winfo_rgb(w["bg"])) < 3 * 40000
                w.config(bg=_mix(
                    w, w["bg"], "#FFFFFF" if dark else "#000000", 0.14
                ))
        except Exception:
            pass

    def leave(event):
        try:
            w = event.widget
            if hasattr(w, "_t_saved"):
                w.config(bg=w._t_saved[0], fg=w._t_saved[1])
                del w._t_saved
        except Exception:
            pass

    app.bind_class("Button", "<Enter>", enter, add="+")
    app.bind_class("Button", "<Leave>", leave, add="+")


def _shortcut(app):
    ico = ASSETS / "app.ico"
    marker = ASSETS / ".shortcut_done"
    if not ico.is_file() or marker.exists():
        return
    script = (
        "$i='ICO_PATH';$w=New-Object -ComObject WScript.Shell;"
        "foreach($n in 'Desktop','CommonDesktopDirectory',"
        "'Programs','CommonPrograms'){"
        "$d=[Environment]::GetFolderPath($n);"
        "Get-ChildItem $d -Filter *.lnk -Recurse -EA SilentlyContinue|%{"
        "try{$s=$w.CreateShortcut($_.FullName);"
        "if(($s.TargetPath+$s.Arguments) -match 'fn_master'){"
        "$s.IconLocation=($i+',0');$s.Save()}}catch{}}}"
    ).replace("ICO_PATH", str(ico))
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
         "-Command", script],
        creationflags=subprocess.CREATE_NO_WINDOW
    )
    marker.write_text("ok", encoding="utf-8")


def apply(app):
    """Вызывается из App.__init__ сразу после _build_ui()."""
    for step in (_app_id, _icon, _ttk, _backdrop, _top, _cards, _panels,
                 _terminal, _hover, _shortcut):
        try:
            step(app)
        except Exception:
            pass  # ошибка оформления не должна мешать работе с ККТ