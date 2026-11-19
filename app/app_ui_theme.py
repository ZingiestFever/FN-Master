"""
ui_theme.py — оформление FN Master.

Пока это заготовка: внешний вид не меняется.
Весь будущий редизайн (цвета, шрифты, стили) будет жить здесь,
а fn_master.py останется отвечать только за механику.
"""

PALETTE = {
    "bg":        "#F1F5F9",
    "panel":     "#FFFFFF",
    "dark":      "#0F172A",
    "accent":    "#38BDF8",
    "primary":   "#2563EB",
    "success":   "#16A34A",
    "warning":   "#EA580C",
    "danger":    "#DC2626",
    "text":      "#0F172A",
    "muted":     "#64748B",
}

FONTS = {
    "base":  ("Segoe UI", 9),
    "bold":  ("Segoe UI", 9, "bold"),
    "title": ("Segoe UI", 12, "bold"),
    "mono":  ("Consolas", 10),
}


def apply(app):
    """Вызывается из App.__init__ сразу после _build_ui()."""
    return None