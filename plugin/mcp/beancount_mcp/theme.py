"""Design tokens for the reports: the HTML page and the openpyxl workbook.

Reports are documents, so they sit on PAPER (`LIGHT`). Ledger green/red colour
signed values only, always with a sign or parentheses. `xl()` turns a hex into
the RRGGBB form openpyxl wants.
"""

PALETTE = {
    "ink": "#143A73",
    "accent": "#1E6FC6",
    "sky": "#5AA9F0",
    "paper": "#FFFFFF",
    "surface": "#F7F8FA",
    "muted": "#5C6B7A",
    "hairline": "#E4E0D8",
    "wash": "#F3F5F8",
    "deep": "#0D1F3D",
    "deep_text": "#DCE6F2",
    "deep_muted": "#8FA3BE",
    "deep_hairline": "#22375C",
    "deep_wash": "#1E2F4B",
    "pos": "#2E7D52",
    "neg": "#A3341F",
    "deep_pos": "#3FBF7F",
    "deep_neg": "#FF7A6B",
}

# Semantic roles on paper (documents, reports, print) and on deep (the app).
LIGHT = {
    "ground": "#FFFFFF",
    "wash": "#F3F5F8",
    "text": "#143A73",
    "text_muted": "#5C6B7A",
    "rule": "#E4E0D8",
    "rule_strong": "#5C6B7A",
    "rule_close": "#143A73",
    "interactive": "#1E6FC6",
    "focus": "#1E6FC6",
    "fill": "#143A73",
    "on_fill": "#FFFFFF",
    "fill_hover": "#1E6FC6",
    "on_fill_hover": "#FFFFFF",
    "fill_active": "#0D1F3D",
    "on_fill_active": "#FFFFFF",
    "ledger_pos": "#2E7D52",
    "ledger_neg": "#A3341F",
    "ledger_zero": "#5C6B7A",
    "mark_letters": "#143A73",
    "mark_period": "#1E6FC6",
}

DARK = {
    "ground": "#0D1F3D",
    "wash": "#1E2F4B",
    "text": "#DCE6F2",
    "text_muted": "#8FA3BE",
    "rule": "#22375C",
    "rule_strong": "#8FA3BE",
    "rule_close": "#DCE6F2",
    "interactive": "#5AA9F0",
    "focus": "#5AA9F0",
    "fill": "#5AA9F0",
    "on_fill": "#0D1F3D",
    "fill_hover": "#DCE6F2",
    "on_fill_hover": "#0D1F3D",
    "fill_active": "#1E6FC6",
    "on_fill_active": "#FFFFFF",
    "ledger_pos": "#3FBF7F",
    "ledger_neg": "#FF7A6B",
    "ledger_zero": "#8FA3BE",
    "mark_letters": "#FFFFFF",
    "mark_period": "#5AA9F0",
}

TINTS = ["#DCE6F2","#B9CFEA","#8FB4E1","#5A93D3","#2F6FBF","#1E5AA3","#143A73"]

THEME_COLOR = "#0D1F3D"

FONT_DISPLAY = "\"Work Sans\", system-ui, sans-serif"
FONT_BODY = "Inter, system-ui, sans-serif"
FONT_MONO = "\"IBM Plex Mono\", ui-monospace, Menlo, monospace"

# (size px, line-height, tracking, weight, face)
TYPE = {
    "display": (74, 1.2, "-0.04em", 800, "display"),
    "h1": (46, 1.2, "-0.03em", 700, "display"),
    "h2": (32, 1.25, "-0.02em", 700, "display"),
    "h3": (26, 1.3, "-0.02em", 700, "display"),
    "h4": (20, 1.35, "-0.01em", 700, "display"),
    "lead": (23, 1.55, "0", 400, "body"),
    "body_lg": (18, 1.6, "0", 400, "body"),
    "body": (16, 1.6, "0", 400, "body"),
    "body_sm": (14, 1.55, "0", 400, "body"),
    "body_xs": (13, 1.5, "0", 400, "body"),
    "figure_xl": (32, 1.3, "-0.02em", 500, "mono"),
    "figure_lg": (20, 1.3, "0", 500, "mono"),
    "figure": (16, 1.3, "0", 400, "mono"),
    "figure_sm": (14, 1.3, "0", 400, "mono"),
    "label": (11, 1.3, "0.14em", 500, "mono"),
    "micro": (14, 1.4, "0", 400, "mono"),
}

# The bundled faces, as (family, file, weight, format). The files sit in
# fonts/ beside this module (SIL OFL 1.1, licences alongside), so a report can
# embed them and render in the same faces with no network.
FONT_FILES = (
    ("Work Sans", "work-sans-latin.woff2", "700 800", "woff2-variations"),
    ("Inter", "inter-latin.woff2", "400 600", "woff2-variations"),
    ("IBM Plex Mono", "ibm-plex-mono-400-latin.woff2", "400", "woff2"),
    ("IBM Plex Mono", "ibm-plex-mono-500-latin.woff2", "500", "woff2"),
    ("IBM Plex Mono", "ibm-plex-mono-600-latin.woff2", "600", "woff2"),
)
FONT_UNICODE_RANGE = "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD"

RULE_HAIR_PX = 1
RULE_CLOSE_PX = 2
RADIUS_PX = 2


def xl(hex_colour: str) -> str:
    """'#143A73' -> '143A73' (openpyxl Font/PatternFill colours)."""
    return hex_colour.lstrip("#").upper()
