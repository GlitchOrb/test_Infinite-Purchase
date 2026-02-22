"""디자인 토큰 — 토스 증권 스타일 가이드."""

# ─── Palette ───
COLOR = {
    # Backgrounds
    "bg":               "#F7F8FA",
    "surface":          "#FFFFFF",
    # Text hierarchy
    "text":             "#191F28",
    "text_secondary":   "#4E5968",
    "text_muted":       "#8B95A1",
    "text_disabled":    "#B0B8C1",
    # Borders
    "border":           "#E5E8EB",
    "border_light":     "#F2F4F6",
    # Primary action
    "primary":          "#3182F6",
    "primary_hover":    "#1B64DA",
    "primary_pressed":  "#1957C2",
    "primary_light":    "#EBF3FE",
    # Semantic
    "error":            "#E05A5A",
    "error_bg":         "#FFF0F1",
    "success":          "#32A85C",
    "success_bg":       "#EAFBF0",
    "warning":          "#FF9200",
    # Trading specific
    "buy_bg":           "#FFF0F1",
    "buy_border":       "#FFD2D6",
    "buy_text":         "#F04452",
    "sell_bg":          "#EBF3FE",
    "sell_border":      "#3182F6",
    "sell_text":        "#3182F6",
    # Neutral / secondary buttons
    "neutral":          "#F2F4F6",
    "neutral_hover":    "#E5E8EB",
    "neutral_pressed":  "#D1D6DB",
    "neutral_text":     "#4E5968",
}

# ─── Typography ───
FONT_FAMILY = '"Noto Sans KR", "Pretendard", "Segoe UI", "Malgun Gothic", sans-serif'

TYPOGRAPHY = {
    "large_title": {"size": 22, "weight": "Bold"},
    "title":       {"size": 18, "weight": "Bold"},
    "subtitle":    {"size": 14, "weight": "DemiBold"},
    "body":        {"size": 14, "weight": "Normal"},
    "label":       {"size": 12, "weight": "DemiBold"},
    "caption":     {"size": 11, "weight": "Normal"},
    "small":       {"size": 10, "weight": "Normal"},
}

# ─── Spacing ───
SPACING = {
    "xs":  4,
    "sm":  8,
    "md":  12,
    "lg":  16,
    "xl":  24,
    "xxl": 32,
    "xxxl": 40,
}

# ─── Radius ───
RADIUS = {
    "card":   20,
    "dialog": 24,
    "button": 14,
    "input":  12,
    "toast":  14,
    "tag":    8,
}

# ─── Sizing ───
SIZE = {
    "button_height":     48,
    "input_height":      48,
    "card_padding":      24,
    "card_padding_lg":   36,
    "dialog_max_width":  460,
    "login_card_min":    380,
    "login_card_max":    440,
}
