"""
PDF generation for the Due Diligence report.
Styled to match the sample: dark navy headers, professional tables, risk indicators.
"""

import io
import os
from typing import Any

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate,
    FrameBreak,
    HRFlowable,
    Image,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.frames import Frame

# ── Colour palette ────────────────────────────────────────────────────────────
# Risika-inspired editorial palette. Variable navne bevares så downstream-kode
# ikke behøver ændringer — kun værdierne er opdateret.
NAVY       = HexColor("#0B1F1A")   # ink — header/cover background
NAVY2      = HexColor("#0F3D34")   # forest — subtle depth on dark bg
GOLD       = HexColor("#E8C547")   # accent yellow
BLUE       = HexColor("#0F3D34")   # forest (reuses "BLUE" slot for primary)
BLUE_LIGHT = HexColor("#2F6F5E")   # moss
GREEN      = HexColor("#2E7D5A")   # ok
ORANGE     = HexColor("#C67A1C")   # warn
RED        = HexColor("#B0392B")   # bad
GRAY       = HexColor("#5A6B65")   # muted
ROW_ALT    = HexColor("#FBF7EC")   # paper — table zebra
BORDER     = HexColor("#D3CCB4")   # outline-variant
CREAM      = HexColor("#F4EEDB")
SAGE       = HexColor("#C8DCCB")
WHITE      = colors.white
BLACK      = colors.black

# ── Typography — ReportLab built-ins der approximerer Geist / Instrument Serif
BODY_FONT        = "Helvetica"
BODY_FONT_BOLD   = "Helvetica-Bold"
BODY_FONT_ITALIC = "Helvetica-Oblique"
SERIF_FONT       = "Times-Roman"
SERIF_FONT_BOLD  = "Times-Bold"
SERIF_FONT_ITAL  = "Times-Italic"

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm

# ── Number formatting helper ─────────────────────────────────────────────────

def _fmt_dkk(val) -> str:
    """Format t.DKK value with thousand separator. Returns '—' for None/empty."""
    if val is None or val == "" or val == "-":
        return "—"
    s = str(val).strip()
    if not s or s in ("—", "null", "None"):
        return "—"
    return s


def _dkk_cell(val, style_name: str = "tbl_cell") -> "Paragraph":
    """Paragraph for a DKK amount — red if negative."""
    s = _fmt_dkk(val)
    if s != "—" and (s.startswith("-") or s.startswith("−")):
        return Paragraph(f'<font color="{RED.hexval()}">{s}</font>', _style(style_name))
    return Paragraph(s, _style(style_name))


# ── Risk indicator helpers ────────────────────────────────────────────────────
RISK_SYMBOL = {
    "lav":    ("✓", GREEN),
    "middel": ("■", ORANGE),
    "høj":    ("▲", RED),
    "ukendt": ("—", GRAY),
}


def _risk(level: str, label: str) -> Paragraph:
    sym, col = RISK_SYMBOL.get(level, ("—", GRAY))
    hex_col = col.hexval() if hasattr(col, "hexval") else "#5A6B65"
    return Paragraph(
        f'<font color="{hex_col}"><b>{sym} {label}</b></font>',
        _style("body_small"),
    )


# ── Style registry ────────────────────────────────────────────────────────────
_STYLES: dict[str, ParagraphStyle] = {}


def _style(name: str) -> ParagraphStyle:
    if not _STYLES:
        _build_styles()
    return _STYLES[name]


def _build_styles() -> None:
    base = getSampleStyleSheet()
    defs = {
        "cover_label": ParagraphStyle(
            "cover_label",
            fontName=BODY_FONT,
            fontSize=8,
            textColor=HexColor("#8FA398"),   # soft green on ink cover
            spaceAfter=0,
            leading=10,
            letterSpacing=1.5,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            fontName=SERIF_FONT,             # editorial serif for the cover title
            fontSize=38,
            textColor=CREAM,
            spaceAfter=6,
            leading=42,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            fontName=BODY_FONT,
            fontSize=13,
            textColor=CREAM,
            spaceAfter=4,
            leading=18,
        ),
        "cover_date": ParagraphStyle(
            "cover_date",
            fontName=BODY_FONT,
            fontSize=12,
            textColor=GOLD,
            spaceAfter=0,
            leading=16,
        ),
        "cover_purpose": ParagraphStyle(
            "cover_purpose",
            fontName=BODY_FONT,
            fontSize=9,
            textColor=HexColor("#0B1F1A"),
            spaceAfter=3,
            leading=13,
        ),
        "section_hdr": ParagraphStyle(
            "section_hdr",
            fontName=BODY_FONT_BOLD,
            fontSize=10,
            textColor=CREAM,
            spaceAfter=0,
            leading=14,
        ),
        "sub_hdr": ParagraphStyle(
            "sub_hdr",
            fontName=SERIF_FONT_BOLD,        # editorial sub-headings
            fontSize=13,
            textColor=BLUE,                  # forest
            spaceBefore=10,
            spaceAfter=4,
            leading=16,
        ),
        "person_hdr": ParagraphStyle(
            "person_hdr",
            fontName=BODY_FONT_BOLD,
            fontSize=9,
            textColor=BLUE,
            spaceBefore=6,
            spaceAfter=2,
            leading=13,
        ),
        "body": ParagraphStyle(
            "body",
            fontName=BODY_FONT,
            fontSize=9,
            textColor=HexColor("#0B1F1A"),
            spaceAfter=6,
            leading=13,
        ),
        "body_small": ParagraphStyle(
            "body_small",
            fontName=BODY_FONT,
            fontSize=8.5,
            textColor=HexColor("#0B1F1A"),
            spaceAfter=3,
            leading=12,
        ),
        "italic_small": ParagraphStyle(
            "italic_small",
            fontName=BODY_FONT_ITALIC,
            fontSize=8,
            textColor=GRAY,
            spaceAfter=4,
            leading=11,
        ),
        "callout": ParagraphStyle(
            "callout",
            fontName=BODY_FONT,
            fontSize=8.5,
            textColor=HexColor("#0B1F1A"),
            spaceAfter=3,
            leading=13,
            leftIndent=8,
            rightIndent=8,
        ),
        "hdr_left": ParagraphStyle(
            "hdr_left",
            fontName="Helvetica",
            fontSize=7,
            textColor=WHITE,
            leading=9,
        ),
        "hdr_right": ParagraphStyle(
            "hdr_right",
            fontName="Helvetica",
            fontSize=7,
            textColor=WHITE,
            leading=9,
            alignment=TA_RIGHT,
        ),
        "ftr_left": ParagraphStyle(
            "ftr_left",
            fontName="Helvetica",
            fontSize=7,
            textColor=GRAY,
            leading=9,
        ),
        "ftr_right": ParagraphStyle(
            "ftr_right",
            fontName="Helvetica",
            fontSize=7,
            textColor=GRAY,
            leading=9,
            alignment=TA_RIGHT,
        ),
        "tbl_hdr": ParagraphStyle(
            "tbl_hdr",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            textColor=WHITE,
            leading=11,
        ),
        "tbl_cell": ParagraphStyle(
            "tbl_cell",
            fontName="Helvetica",
            fontSize=8.5,
            textColor=BLACK,
            leading=11,
        ),
        "tbl_bold": ParagraphStyle(
            "tbl_bold",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            textColor=BLACK,
            leading=11,
        ),
    }
    _STYLES.update(defs)


# ── Page templates ────────────────────────────────────────────────────────────

def _make_page_templates(doc: BaseDocTemplate, meta: dict) -> list[PageTemplate]:
    company  = meta.get("selskabsnavn", "")
    cvr      = meta.get("cvr", "")
    rap_dato = meta.get("rapport_dato", "")

    def cover_bg(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
        # Accent bar at top
        canvas.setFillColor(GOLD)
        canvas.rect(0, PAGE_H - 4 * mm, PAGE_W, 4 * mm, fill=1, stroke=0)
        canvas.restoreState()

    def content_hdr_ftr(canvas, doc):
        canvas.saveState()
        # Header bar
        canvas.setFillColor(NAVY2)
        canvas.rect(0, PAGE_H - 1.2 * cm, PAGE_W, 1.2 * cm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(WHITE)
        canvas.drawString(MARGIN, PAGE_H - 0.75 * cm,
                          "FORTROLIGT — KUNDE DUE DILIGENCE RAPPORT")
        right_txt = f"{company}  |  CVR {cvr}"
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.75 * cm, right_txt)

        # Footer line
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 1.2 * cm, PAGE_W - MARGIN, 1.2 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRAY)
        canvas.drawString(MARGIN, 0.7 * cm,
                          f"Udarbejdet {rap_dato}  |  Fortroligt dokument")
        canvas.drawRightString(PAGE_W - MARGIN, 0.7 * cm,
                               f"Side {doc.page}")
        canvas.restoreState()

    cover_frame = Frame(0, 0, PAGE_W, PAGE_H, leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0, id="cover")
    content_frame = Frame(MARGIN, 1.8 * cm, PAGE_W - 2 * MARGIN,
                          PAGE_H - 1.2 * cm - 1.8 * cm,
                          leftPadding=0, rightPadding=0,
                          topPadding=0.4 * cm, bottomPadding=0, id="content")

    return [
        PageTemplate(id="cover",   frames=[cover_frame],   onPage=cover_bg),
        PageTemplate(id="content", frames=[content_frame], onPage=content_hdr_ftr),
    ]


# ── Section header ────────────────────────────────────────────────────────────

def _section_hdr(number: str, title: str) -> Table:
    text = Paragraph(f"{number}  {title}", _style("section_hdr"))
    tbl = Table([[text]], colWidths=[PAGE_W - 2 * MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
    ]))
    return tbl


# ── Generic key-value table ───────────────────────────────────────────────────

def _kv_table(rows: list[tuple[str, str]], col_widths=None) -> Table:
    if col_widths is None:
        col_widths = [5.5 * cm, PAGE_W - 2 * MARGIN - 5.5 * cm]
    data = []
    for i, (k, v) in enumerate(rows):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        data.append([
            Paragraph(f"<b>{k}</b>", _style("tbl_cell")),
            Paragraph(str(v), _style("tbl_cell")),
        ])
    tbl = Table(data, colWidths=col_widths)
    style_cmds = [
        ("GRID",       (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(len(rows)):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        style_cmds.append(("BACKGROUND", (0, i), (-1, i), bg))
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _data_table(headers: list[str], rows: list[list], col_widths=None) -> Table:
    content_w = PAGE_W - 2 * MARGIN
    if col_widths is None:
        n = len(headers)
        col_widths = [content_w / n] * n

    hdr_row = [Paragraph(h, _style("tbl_hdr")) for h in headers]
    data_rows = []
    for row in rows:
        data_rows.append([Paragraph(str(cell), _style("tbl_cell")) for cell in row])

    all_data = [hdr_row] + data_rows
    tbl = Table(all_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID",       (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i, _ in enumerate(rows):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        style_cmds.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _callout(text: str, bold_prefix: str = "") -> Table:
    """Blue left-border callout box."""
    if bold_prefix:
        para = Paragraph(f"<b>{bold_prefix}</b> {text}", _style("callout"))
    else:
        para = Paragraph(text, _style("callout"))
    tbl = Table([[para]], colWidths=[PAGE_W - 2 * MARGIN])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), SAGE),
        ("LINEBEFORE", (0, 0), (0, -1), 4, BLUE),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING",   (0, 0), (-1, -1), 12),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    return tbl


# ── Cover page ────────────────────────────────────────────────────────────────

def _build_cover(data: dict) -> list:
    meta   = data["meta"]
    risiko = data.get("risiko_oversigt", [])
    s01    = data.get("sektion_01", {})

    content_w = PAGE_W - 2 * MARGIN
    story = []

    # Top padding
    story.append(Spacer(1, 1.8 * cm))

    # Label
    story.append(Paragraph(
        "KUNDE DUE DILIGENCE  |  REVISOR ONBOARDING",
        ParagraphStyle("cl", fontName=BODY_FONT, fontSize=8,
                       textColor=HexColor("#8FA398"), leading=10, leftIndent=MARGIN)
    ))
    story.append(Spacer(1, 0.6 * cm))

    # Company name
    story.append(Paragraph(
        meta["selskabsnavn"],
        ParagraphStyle("ct", fontName=SERIF_FONT, fontSize=38,
                       textColor=WHITE, leading=36, leftIndent=MARGIN)
    ))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph(
        f"CVR-nr. {meta['cvr'][:2]} {meta['cvr'][2:4]} {meta['cvr'][4:6]} {meta['cvr'][6:]}",
        ParagraphStyle("cs", fontName="Helvetica", fontSize=13,
                       textColor=WHITE, leading=18, leftIndent=MARGIN)
    ))

    story.append(Paragraph(
        meta.get("adresse", ""),
        ParagraphStyle("cs2", fontName="Helvetica", fontSize=11,
                       textColor=WHITE, leading=16, leftIndent=MARGIN)
    ))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        f"Rapport udarbejdet: {meta['rapport_dato']}",
        ParagraphStyle("cd", fontName="Helvetica", fontSize=11,
                       textColor=GOLD, leading=16, leftIndent=MARGIN)
    ))

    # Horizontal rule
    story.append(Spacer(1, 0.5 * cm))
    story.append(HRFlowable(width=content_w - 2 * MARGIN, thickness=1.5,
                             color=BLUE_LIGHT, spaceAfter=8))

    # Confidentiality line
    story.append(Paragraph(
        "Fortroligt  |  Intern brug — revisor / onboarding",
        ParagraphStyle("conf", fontName="Helvetica", fontSize=8,
                       textColor=GRAY, leading=10, leftIndent=MARGIN)
    ))
    story.append(Spacer(1, 0.8 * cm))

    # Purpose box
    purpose_tbl = Table(
        [[Paragraph(
            "<b>Formål med rapporten:</b> Denne due diligence er udarbejdet som led i "
            "revisorens onboarding-procedure for ny kunde. Rapporten sammenstiller offentligt "
            "tilgængelig information om "
            f"{meta['selskabsnavn']} — herunder CVR-data, selskabsstruktur, ledelse og reelle "
            "ejere (UBO), finansielle nøgletal, nyhedsovervågning og brancheanalyse. "
            "Rapporten erstatter ikke en fuld AML-risikovurdering, men udgør et dokumenteret "
            "grundlag herfor.",
            ParagraphStyle("pp", fontName=BODY_FONT, fontSize=9,
                           textColor=HexColor("#0B1F1A"), leading=13,
                           leftIndent=0, rightIndent=0)
        )]],
        colWidths=[content_w - 2 * MARGIN + 0.4 * cm],
        style=TableStyle([
            ("BACKGROUND",    (0, 0), (-1, -1), ROW_ALT),
            ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
            ("LEFTPADDING",   (0, 0), (-1, -1), 10),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]),
    )
    story.append(Table([[purpose_tbl]], colWidths=[PAGE_W],
                       style=TableStyle([
                           ("LEFTPADDING",  (0, 0), (-1, -1), MARGIN),
                           ("RIGHTPADDING", (0, 0), (-1, -1), MARGIN),
                           ("TOPPADDING",   (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
                       ])))
    story.append(Spacer(1, 0.6 * cm))

    # Risk summary table
    sym_map = {"lav": "✓", "middel": "■", "høj": "▲", "ukendt": "—"}
    col_map = {
        "lav": f'<font color="{GREEN.hexval()}">',
        "middel": f'<font color="{ORANGE.hexval()}">',
        "høj": f'<font color="{RED.hexval()}">',
        "ukendt": f'<font color="{GRAY.hexval()}">',
    }

    hdr = [
        Paragraph("<b>Risikoindikator</b>", _style("tbl_hdr")),
        Paragraph("<b>Vurdering</b>", _style("tbl_hdr")),
        Paragraph("<b>Bemærkning</b>", _style("tbl_hdr")),
    ]
    # Kompakt stil til forsidebordet (lidt mindre for at passe på siden)
    cover_cell = ParagraphStyle("cover_cell", fontName="Helvetica",
                                fontSize=7.8, textColor=BLACK, leading=10)
    cover_hdr  = ParagraphStyle("cover_hdr",  fontName="Helvetica-Bold",
                                fontSize=7.8, textColor=WHITE,  leading=10)

    hdr = [
        Paragraph("<b>Risikoindikator</b>", cover_hdr),
        Paragraph("<b>Vurdering</b>",        cover_hdr),
        Paragraph("<b>Bemærkning</b>",        cover_hdr),
    ]
    rows_data = [hdr]
    for i, r in enumerate(risiko):
        lvl = r.get("vurdering", "ukendt")
        sym = sym_map.get(lvl, "—")
        col_open = col_map.get(lvl, col_map["ukendt"])
        # Kap bemærkning til maks 90 tegn på forsiden — fuld tekst i sektion 09
        bemaerk = r.get("bemærkning", "")
        if len(bemaerk) > 90:
            bemaerk = bemaerk[:87] + "…"
        rows_data.append([
            Paragraph(r.get("indikator", ""), cover_cell),
            Paragraph(f"{col_open}<b>{sym} {r.get('status_tekst','')}</b></font>",
                      cover_cell),
            Paragraph(bemaerk, cover_cell),
        ])

    cw = content_w - 2 * MARGIN + 0.4 * cm
    risk_tbl = Table(rows_data,
                     colWidths=[cw * 0.35, cw * 0.30, cw * 0.35],
                     repeatRows=1)
    risk_style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("GRID",       (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(len(risiko)):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        risk_style.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
    risk_tbl.setStyle(TableStyle(risk_style))

    story.append(Table([[risk_tbl]], colWidths=[PAGE_W],
                       style=TableStyle([
                           ("LEFTPADDING",  (0, 0), (-1, -1), MARGIN),
                           ("RIGHTPADDING", (0, 0), (-1, -1), MARGIN),
                           ("TOPPADDING",   (0, 0), (-1, -1), 0),
                           ("BOTTOMPADDING",(0, 0), (-1, -1), 0),
                       ])))

    return story


# ── Section builders ──────────────────────────────────────────────────────────

def _build_s01(s: dict) -> list:
    story = [_section_hdr("01", "CVR-DATA OG SELSKABSSTRUKTUR"), Spacer(1, 6)]
    rows = [
        ("Selskabsnavn",       s.get("selskabsnavn", "")),
        ("CVR-nummer",         s.get("cvr_nummer", "")),
        ("Selskabsform",       s.get("selskabsform", "")),
        ("Adresse",            s.get("adresse", "")),
        ("Stiftelsesdato",     s.get("stiftelsesdato", "")),
        ("Branchekode",        s.get("branchekode", "")),
        ("Formål",             s.get("formaal", "")),
        ("Status",             s.get("status", "")),
        ("Reklamebeskyttet",   s.get("reklamebeskyttet", "")),
        ("Regnskabspligt",     s.get("regnskabspligt", "")),
        ("Website",            s.get("website", "")),
        ("Seneste regnskabsår",s.get("seneste_regnskabsaar") or "—"),
        ("Næste regnskabsfrist",s.get("naeste_regnskabsfrist") or "—"),
    ]
    story.append(_kv_table(rows))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Selskabsbeskrivelse", _style("sub_hdr")))
    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceAfter=4))
    story.append(Paragraph(s.get("selskabsbeskrivelse", ""), _style("body")))
    story.append(Spacer(1, 6))

    kontorer = s.get("kontorer", [])
    if kontorer:
        story.append(Paragraph("Kontorer og international tilstedeværelse", _style("sub_hdr")))
        cw = PAGE_W - 2 * MARGIN
        story.append(_data_table(
            ["Land / By", "Funktion"],
            [[k.get("land_by", ""), k.get("funktion", "")] for k in kontorer],
            col_widths=[cw * 0.5, cw * 0.5],
        ))
        story.append(Paragraph(
            "Bemærk: Udenlandske juridiske enheder er ikke verificeret i dansk CVR. "
            "Revisor bør indhente dokumentation for eventuelle udenlandske selskabsstrukturer.",
            _style("italic_small"),
        ))
    return story


def _build_s02(s: dict) -> list:
    story = [_section_hdr("02", "LEDELSE, BESTYRELSE OG REELLE EJERE (UBO)"), Spacer(1, 6)]

    story.append(Paragraph("2.1 Direktionen", _style("sub_hdr")))
    direktion = s.get("direktion", [])
    cw = PAGE_W - 2 * MARGIN
    story.append(_data_table(
        ["Navn", "Rolle", "Tilknyttet siden", "Bemærkning"],
        [[d.get("navn",""), d.get("rolle",""), d.get("tilknyttet_siden",""), d.get("bemaerkning","")] for d in direktion],
        col_widths=[cw*0.28, cw*0.22, cw*0.18, cw*0.32],
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("2.2 Reelle ejere — UBO-register (hvidvaskloven)", _style("sub_hdr")))
    ubo_note = s.get("ubo_note", "")
    if ubo_note:
        story.append(_callout(ubo_note, "Hvidvasklov-krav:"))
        story.append(Spacer(1, 4))

    reelle = s.get("reelle_ejere", [])
    story.append(_data_table(
        ["Navn", "Ejerandel", "Stemmeandel", "Besiddelse", "Dato registreret"],
        [[r.get("navn",""), r.get("ejerandel",""), r.get("stemmeandel",""),
          r.get("besiddelse",""), r.get("dato_registreret","")] for r in reelle],
        col_widths=[cw*0.32, cw*0.13, cw*0.13, cw*0.17, cw*0.25],
    ))
    story.append(Paragraph(
        "Ejerandelene bør verificeres via selskabets ejerbog og aktionæroverenskomst.",
        _style("italic_small"),
    ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("2.3 Ledelses- og ejerprofiler", _style("sub_hdr")))
    for lp in s.get("ledelsesprofiler", []):
        story.append(Paragraph(f"{lp.get('navn','')} — {lp.get('titel','')}", _style("person_hdr")))
        story.append(Paragraph(lp.get("beskrivelse", ""), _style("body_small")))

    pep_note = s.get("pep_screening_note", "")
    if pep_note:
        story.append(Spacer(1, 4))
        story.append(_callout(pep_note, "PEP-screening:"))

    # ── 2.5 AML/PEP Screening ────────────────────────────────────────────
    story.append(Spacer(1, 8))
    story.append(Paragraph("2.5 AML/PEP Screening", _style("sub_hdr")))
    aml = s.get("aml_screening") or {}
    aml_personer = (aml.get("personer") or []) if isinstance(aml, dict) else []

    risk_col = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
                "høj": RED.hexval(), "Lav": GREEN.hexval(),
                "Middel": ORANGE.hexval(), "Høj": RED.hexval()}

    if aml_personer:
        cw = PAGE_W - 2 * MARGIN
        hdr_aml = [Paragraph(h, _style("tbl_hdr"))
                   for h in ["Navn", "Rolle", "PEP-risiko", "Sanktioner", "Samlet risiko"]]
        aml_rows = [hdr_aml]
        for i, p in enumerate(aml_personer):
            samlet = str(p.get("samlet_risiko") or "Lav")
            col = risk_col.get(samlet, GRAY.hexval())
            bg = ROW_ALT if i % 2 == 0 else WHITE
            aml_rows.append([
                Paragraph(str(p.get("navn") or "—"), _style("tbl_cell")),
                Paragraph(str(p.get("rolle") or "—"), _style("tbl_cell")),
                Paragraph(str(p.get("pep_risiko") or "—"), _style("tbl_cell")),
                Paragraph(str(p.get("sanktioner") or "—"), _style("tbl_cell")),
                Paragraph(f'<font color="{col}"><b>{samlet}</b></font>', _style("tbl_cell")),
            ])
        aml_tbl = Table(aml_rows,
                        colWidths=[cw*0.22, cw*0.16, cw*0.24, cw*0.22, cw*0.16],
                        repeatRows=1)
        aml_ts = [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("GRID",       (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ]
        for i in range(len(aml_personer)):
            bg = ROW_ALT if i % 2 == 0 else WHITE
            aml_ts.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
        aml_tbl.setStyle(TableStyle(aml_ts))
        story.append(aml_tbl)
        story.append(Paragraph(
            "Screening baseret på offentligt tilgængeligt materiale. "
            "Manuel PEP-kontrol mod World-Check eller Experian PEP anbefales.",
            _style("italic_small"),
        ))
    else:
        story.append(_callout(
            "Ingen AML/PEP screeningsdata tilgængeligt. "
            "Manuel screening mod anerkendt PEP/sanktionsdatabase anbefales.",
            "PEP-screening:",
        ))

    return story


def _build_s03(s: dict) -> list:
    story = [_section_hdr("03", "EJERSTRUKTUR OG KONCERNKORT"), Spacer(1, 6)]
    story.append(Paragraph(s.get("beskrivelse", ""), _style("body")))
    story.append(Spacer(1, 6))

    cw = PAGE_W - 2 * MARGIN
    koncern = s.get("koncernkort", [])
    if koncern:
        story.append(_data_table(
            ["NIVEAU", "ENHED", "EJERANDEL", "BEMÆRKNING"],
            [[k.get("niveau",""), k.get("enhed",""), k.get("ejerandel",""), k.get("bemaerkning","")] for k in koncern],
            col_widths=[cw*0.20, cw*0.35, cw*0.18, cw*0.27],
        ))
    anb = s.get("revisor_anbefaling", "")
    if anb:
        story.append(Paragraph(anb, _style("italic_small")))
    return story


def _build_s04(s: dict, selskabsnavn: str = "Selskab") -> list:
    story = [_section_hdr("04", "FINANSIELLE NØGLETAL OG ÅRSREGNSKABER"), Spacer(1, 6)]
    cw = PAGE_W - 2 * MARGIN
    story.append(Paragraph("4.1 Resultatopgørelse — nøgletal", _style("sub_hdr")))

    aar_kolonner = [str(a) for a in (s.get("aar_kolonner") or []) if a]
    reg_rows = s.get("regnskabspost_tabel") or []

    # Detect new format (vaerdier[]) vs. old format (seneste_aar / foregaaende_aar)
    use_new = bool(aar_kolonner and reg_rows and isinstance(reg_rows[0], dict)
                   and "vaerdier" in reg_rows[0])

    if use_new:
        n = len(aar_kolonner)
        post_w = 4.5 * cm
        yr_w   = (cw - post_w) / n
        hdr_row = (
            [Paragraph("<b>Regnskabspost</b>", _style("tbl_hdr"))]
            + [Paragraph(f"<b>{yr}</b>", _style("tbl_hdr")) for yr in aar_kolonner]
        )
        data_rows = []
        for r in reg_rows:
            post  = str(r.get("post") or "")
            vals  = r.get("vaerdier") or []
            cells = [Paragraph(post, _style("tbl_cell"))]
            for i in range(n):
                v = vals[i] if i < len(vals) else None
                cells.append(_dkk_cell(v))
            data_rows.append(cells)
        all_data = [hdr_row] + data_rows
        tbl = Table(all_data, colWidths=[post_w] + [yr_w] * n, repeatRows=1)
        ts = [
            ("BACKGROUND",    (0, 0), (-1,  0), NAVY),
            ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
        ]
        for i, _ in enumerate(data_rows):
            bg = ROW_ALT if i % 2 == 0 else WHITE
            ts.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
        tbl.setStyle(TableStyle(ts))
        story.append(tbl)
    else:
        # Backwards-compatible 2-column fallback
        aar_s = s.get("regnskabsaar_seneste") or "Seneste"
        aar_f = s.get("regnskabsaar_foregaaende") or "Foregående"
        story.append(_data_table(
            ["Regnskabspost", str(aar_s), str(aar_f), "Ændring"],
            [[r.get("post", ""),
              _fmt_dkk(r.get("seneste_aar")),
              _fmt_dkk(r.get("foregaaende_aar")),
              r.get("aendring") or "—"] for r in reg_rows],
            col_widths=[cw * 0.40, cw * 0.22, cw * 0.22, cw * 0.16],
        ))
    story.append(Spacer(1, 8))

    story.append(Paragraph("4.2 Finansiel analyse og vurdering", _style("sub_hdr")))
    story.append(Paragraph(s.get("finansiel_analyse", ""), _style("body")))

    noegle = s.get("noegletal_tabel", [])
    if noegle:
        sym_map = {"lav": "✓ ", "middel": "■ ", "høj": "▲ ", "ukendt": "— "}
        col_map = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
                   "høj": RED.hexval(), "ukendt": GRAY.hexval()}
        hdr = [Paragraph(h, _style("tbl_hdr")) for h in ["Nøgletal", selskabsnavn, "Branche (DK median)", "Vurdering"]]
        rows_data = [hdr]
        for i, n in enumerate(noegle):
            lvl = n.get("vurdering", "ukendt")
            sym = sym_map.get(lvl, "— ")
            col = col_map.get(lvl, GRAY.hexval())
            vur_cell = Paragraph(f'<font color="{col}"><b>{sym}{lvl.capitalize()}</b></font>',
                                 _style("tbl_cell"))
            bg = ROW_ALT if i % 2 == 0 else WHITE
            rows_data.append([
                Paragraph(n.get("noegletal",""), _style("tbl_cell")),
                Paragraph(n.get("selskab",""), _style("tbl_cell")),
                Paragraph(n.get("branche_median",""), _style("tbl_cell")),
                vur_cell,
            ])
        tbl = Table(rows_data, colWidths=[cw*0.28, cw*0.22, cw*0.28, cw*0.22], repeatRows=1)
        ts = [
            ("BACKGROUND", (0,0), (-1,0), NAVY),
            ("GRID",       (0,0), (-1,-1), 0.5, BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
            ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
        ]
        for i in range(len(noegle)):
            bg = ROW_ALT if i % 2 == 0 else WHITE
            ts.append(("BACKGROUND", (0, i+1), (-1, i+1), bg))
        tbl.setStyle(TableStyle(ts))
        story.append(tbl)
    return story


def _paategning_color(typ: str):
    """Return colour for a given påtegningstype."""
    if not typ:
        return GRAY
    t = typ.lower()
    if "forbehold" in t:
        return RED
    if "review" in t or "assistance" in t:
        return ORANGE
    if "uden forbehold" in t or "revisionspåtegning" in t or "revision" in t:
        return GREEN
    return GRAY


def _build_paategninger(s04: dict) -> list:
    """Section 04b — Revisorbemærkninger og påtegningshistorik."""
    story = [_section_hdr("04b", "REVISORBEMÆRKNINGER OG PÅTEGNINGSHISTORIK"), Spacer(1, 6)]

    paat = s04.get("paategninger") or {}
    historik = (paat.get("historik") or []) if isinstance(paat, dict) else []

    if not historik:
        story.append(Paragraph(
            "Ingen påtegningsdata tilgængelig fra officielle regnskaber.",
            _style("italic_small"),
        ))
        return story

    cw = PAGE_W - 2 * MARGIN
    hdr = [Paragraph(h, _style("tbl_hdr"))
           for h in ["År", "Revisor", "Påtegningstype"]]
    rows_data = [hdr]
    for i, h in enumerate(historik):
        typ = str(h.get("paategning") or h.get("påtegning") or "")
        rev = str(h.get("revisor") or "—")
        yr  = str(h.get("aar") or h.get("år") or "—")
        col = _paategning_color(typ)
        bg  = ROW_ALT if i % 2 == 0 else WHITE
        rows_data.append([
            Paragraph(yr, _style("tbl_cell")),
            Paragraph(rev, _style("tbl_cell")),
            Paragraph(f'<font color="{col.hexval()}"><b>{typ or "—"}</b></font>',
                      _style("tbl_cell")),
        ])
    tbl = Table(rows_data, colWidths=[cw * 0.10, cw * 0.40, cw * 0.50], repeatRows=1)
    ts = [
        ("BACKGROUND",    (0, 0), (-1,  0), NAVY),
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i in range(len(historik)):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        ts.append(("BACKGROUND", (0, i + 1), (-1, i + 1), bg))
    tbl.setStyle(TableStyle(ts))
    story.append(tbl)
    return story


def _build_s05(s: dict) -> list:
    story = [_section_hdr("05", "KREDITVURDERING OG BETALINGSADFÆRD"), Spacer(1, 6)]

    note = s.get("kreditnote", "")
    if note:
        story.append(_callout(note, "Bemærk:"))
        story.append(Spacer(1, 6))

    sym_map = {"lav": "✓ ", "middel": "■ ", "høj": "▲ ", "ukendt": "— "}
    col_map = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
               "høj": RED.hexval(), "ukendt": GRAY.hexval()}

    for i, p in enumerate(s.get("kreditpunkter", [])):
        lvl = p.get("vurdering", "ukendt")
        sym = sym_map.get(lvl, "— ")
        col = col_map.get(lvl, GRAY.hexval())
        bg = ROW_ALT if i % 2 == 0 else WHITE
        row_tbl = Table(
            [[Paragraph(f"<b>{p.get('punkt','')}</b>", _style("tbl_cell")),
              Paragraph(f'<font color="{col}"><b>{sym}{p.get("vaerdi","")}</b></font>',
                        _style("tbl_cell"))]],
            colWidths=[(PAGE_W-2*MARGIN)*0.45, (PAGE_W-2*MARGIN)*0.55],
        )
        row_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,-1), bg),
            ("GRID",       (0,0), (-1,-1), 0.5, BORDER),
            ("TOPPADDING",    (0,0), (-1,-1), 5),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ]))
        story.append(row_tbl)
    return story


def _build_s06(s: dict) -> list:
    story = [_section_hdr("06", "NYHEDSOVERVÅGNING OG NEGATIV PRESSE"), Spacer(1, 6)]

    soeg_dato = s.get("soegning_dato", "")
    story.append(Paragraph(
        f"Der er foretaget nyhedssøgning på selskabets navn og ledelse pr. {soeg_dato}. "
        "Søgningen dækkede danske og engelske søgeord på tværs af nyheder og erhvervsmedier.",
        _style("body"),
    ))

    sym_map = {"lav": "✓ ", "middel": "■ ", "høj": "▲ ", "ukendt": "— "}
    col_map = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
               "høj": RED.hexval(), "ukendt": GRAY.hexval()}

    cw = PAGE_W - 2 * MARGIN
    hdr = [Paragraph(h, _style("tbl_hdr")) for h in ["Søgeområde", "Resultat", "Kilde / Metode"]]
    rows_data = [hdr]
    for i, r in enumerate(s.get("soegeresultater", [])):
        res_txt = r.get("resultat", "")
        lvl = "lav" if "ingen" in res_txt.lower() else "ukendt"
        sym = sym_map.get(lvl, "— ")
        col = col_map.get(lvl, GRAY.hexval())
        bg = ROW_ALT if i % 2 == 0 else WHITE
        rows_data.append([
            Paragraph(r.get("omraade",""), _style("tbl_cell")),
            Paragraph(f'<font color="{col}"><b>{sym}{res_txt}</b></font>', _style("tbl_cell")),
            Paragraph(r.get("kilde",""), _style("tbl_cell")),
        ])
    tbl = Table(rows_data, colWidths=[cw*0.35, cw*0.35, cw*0.30], repeatRows=1)
    ts = [
        ("BACKGROUND", (0,0), (-1,0), NAVY),
        ("GRID",       (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]
    for i in range(len(s.get("soegeresultater", []))):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        ts.append(("BACKGROUND", (0,i+1), (-1,i+1), bg))
    tbl.setStyle(TableStyle(ts))
    story.append(tbl)
    story.append(Spacer(1, 8))

    pos = s.get("positive_observationer", "")
    if pos:
        story.append(Paragraph("Positive observationer fra nyhedssøgning", _style("sub_hdr")))
        story.append(Paragraph(pos, _style("body")))
    return story


def _build_s07(s: dict, selskabsnavn: str = "Selskab") -> list:
    story = [_section_hdr("07", "BRANCHEANALYSE OG BENCHMARKING"), Spacer(1, 6)]

    rows = [
        ("Branchekode (DB07)", s.get("branchekode_db07", "")),
        ("NACE-kode",           s.get("nace_kode", "")),
        ("Branchekarakter",     s.get("branchekarakter", "")),
        ("Typisk kundesegment", s.get("typisk_kundesegment", "")),
        ("Markedssituation",    s.get("markedssituation", "")),
    ]
    story.append(_kv_table(rows))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Branche-benchmarking (DK median — NACE 62.01)", _style("sub_hdr")))

    sym_map = {"lav": "✓ I top", "middel": "■ Under median", "høj": "▲ Under median", "ukendt": "— Ukendt"}
    col_map = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
               "høj": RED.hexval(), "ukendt": GRAY.hexval()}

    cw = PAGE_W - 2 * MARGIN
    hdr = [Paragraph(h, _style("tbl_hdr")) for h in ["Nøgletal", "Branche (median)", selskabsnavn, "Vurdering"]]
    rows_data = [hdr]
    for i, b in enumerate(s.get("benchmarking", [])):
        lvl = b.get("vurdering", "ukendt")
        vur = sym_map.get(lvl, "— Ukendt")
        col = col_map.get(lvl, GRAY.hexval())
        bg = ROW_ALT if i % 2 == 0 else WHITE
        rows_data.append([
            Paragraph(b.get("noegletal",""), _style("tbl_cell")),
            Paragraph(b.get("branche_median",""), _style("tbl_cell")),
            Paragraph(b.get("selskab",""), _style("tbl_cell")),
            Paragraph(f'<font color="{col}"><b>{vur}</b></font>', _style("tbl_cell")),
        ])
    tbl = Table(rows_data, colWidths=[cw*0.28, cw*0.24, cw*0.22, cw*0.26], repeatRows=1)
    ts = [
        ("BACKGROUND", (0,0), (-1,0), NAVY),
        ("GRID",       (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]
    for i in range(len(s.get("benchmarking", []))):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        ts.append(("BACKGROUND", (0,i+1), (-1,i+1), bg))
    tbl.setStyle(TableStyle(ts))
    story.append(tbl)

    bnote = s.get("benchmarking_note","")
    if bnote:
        story.append(Paragraph(bnote, _style("italic_small")))
    return story


def _build_s08(s: dict) -> list:
    story = [_section_hdr("08", "LEDELSESPROFILER OG DIGITALT FODAFTRYK"), Spacer(1, 6)]
    story.append(Paragraph(
        "Nedenstående er baseret på offentligt tilgængeligt digitalt indhold. "
        "LinkedIn-profiler er ikke individuelt screenet og bør verificeres separat.",
        _style("body"),
    ))

    cw = PAGE_W - 2 * MARGIN
    for lp in s.get("ledelsesprofiler", []):
        story.append(Paragraph(f"{lp.get('navn','')} — {lp.get('titel','')}", _style("person_hdr")))
        for punkt in lp.get("punkter", []):
            story.append(Paragraph(f"• {punkt}", _style("body_small")))

        # Compact selskabsportefølje tabel
        selskaber = lp.get("selskaber") or []
        if selskaber:
            story.append(Spacer(1, 3))
            hdr_s = [Paragraph(h, _style("tbl_hdr"))
                     for h in ["Selskabsnavn", "CVR", "Status", "Rolle", "Periode"]]
            sel_rows = [hdr_s]
            shown = selskaber[:5]
            extra = len(selskaber) - len(shown)
            for j, sel in enumerate(shown):
                status = str(sel.get("status") or "")
                is_bankrupt = (sel.get("konkurs") is True
                               or "KONKURS" in status.upper())
                bg = HexColor("#fdecea") if is_bankrupt else (ROW_ALT if j % 2 == 0 else WHITE)
                status_txt = status or "—"
                if is_bankrupt:
                    status_cell = Paragraph(
                        f'<font color="{RED.hexval()}"><b>{status_txt}</b></font>',
                        _style("tbl_cell"))
                else:
                    status_cell = Paragraph(status_txt, _style("tbl_cell"))
                sel_rows.append([
                    Paragraph(str(sel.get("navn") or "—"), _style("tbl_cell")),
                    Paragraph(str(sel.get("cvr") or "—"), _style("tbl_cell")),
                    status_cell,
                    Paragraph(str(sel.get("rolle") or "—"), _style("tbl_cell")),
                    Paragraph(str(sel.get("periode") or "—"), _style("tbl_cell")),
                ])
            sel_tbl = Table(sel_rows,
                            colWidths=[cw*0.35, cw*0.13, cw*0.20, cw*0.17, cw*0.15],
                            repeatRows=1)
            sel_ts = [
                ("BACKGROUND",    (0, 0), (-1,  0), NAVY),
                ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
                ("TOPPADDING",    (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING",   (0, 0), (-1, -1), 5),
                ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ]
            for j in range(len(shown)):
                status = str(shown[j].get("status") or "")
                is_bankrupt = (shown[j].get("konkurs") is True
                               or "KONKURS" in status.upper())
                bg = HexColor("#fdecea") if is_bankrupt else (ROW_ALT if j % 2 == 0 else WHITE)
                sel_ts.append(("BACKGROUND", (0, j + 1), (-1, j + 1), bg))
            sel_tbl.setStyle(TableStyle(sel_ts))
            story.append(sel_tbl)
            if extra > 0:
                story.append(Paragraph(f"… og {extra} øvrige selskaber",
                                       _style("italic_small")))
        story.append(Spacer(1, 4))

    anb = s.get("anbefaling", "")
    if anb:
        story.append(_callout(anb, "Anbefaling:"))
    return story


def _build_s09(s: dict) -> list:
    story = [_section_hdr("09", "SAMLET RISIKOVURDERING OG ANBEFALINGER"), Spacer(1, 6)]
    story.append(Paragraph(s.get("samlet_vurdering", ""), _style("body")))
    story.append(Spacer(1, 6))

    sym_map = {"lav": "Lav", "middel": "Middel", "høj": "Høj", "ukendt": "Ukendt"}
    col_map = {"lav": GREEN.hexval(), "middel": ORANGE.hexval(),
               "høj": RED.hexval(), "ukendt": GRAY.hexval()}

    cw = PAGE_W - 2 * MARGIN
    hdr = [Paragraph(h, _style("tbl_hdr")) for h in ["Kategori", "Risiko", "Handling"]]
    rows_data = [hdr]
    for i, r in enumerate(s.get("risikohandlinger", [])):
        lvl = r.get("risiko", "ukendt")
        col = col_map.get(lvl, GRAY.hexval())
        label = sym_map.get(lvl, "Ukendt")
        bg = ROW_ALT if i % 2 == 0 else WHITE
        rows_data.append([
            Paragraph(r.get("kategori",""), _style("tbl_cell")),
            Paragraph(f'<font color="{col}"><b>{label}</b></font>', _style("tbl_cell")),
            Paragraph(r.get("handling",""), _style("tbl_cell")),
        ])
    tbl = Table(rows_data, colWidths=[cw*0.28, cw*0.15, cw*0.57], repeatRows=1)
    ts = [
        ("BACKGROUND", (0,0), (-1,0), NAVY),
        ("GRID",       (0,0), (-1,-1), 0.5, BORDER),
        ("TOPPADDING",    (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("LEFTPADDING",   (0,0), (-1,-1), 6),
        ("VALIGN",        (0,0), (-1,-1), "MIDDLE"),
    ]
    for i in range(len(s.get("risikohandlinger", []))):
        bg = ROW_ALT if i % 2 == 0 else WHITE
        ts.append(("BACKGROUND", (0,i+1), (-1,i+1), bg))
    tbl.setStyle(TableStyle(ts))
    story.append(tbl)
    story.append(Spacer(1, 10))

    ansvars = s.get("ansvarsfraskrivelse", "")
    if ansvars:
        story.append(_callout(ansvars, "Ansvarsfraskrivelse:"))
    return story


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_pdf(report_data: dict) -> bytes:
    """Build the complete PDF and return raw bytes."""
    buf = io.BytesIO()
    meta = report_data.get("meta", {})

    doc = BaseDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=0,
        rightMargin=0,
        topMargin=0,
        bottomMargin=0,
    )
    doc.addPageTemplates(_make_page_templates(doc, meta))

    # Ensure styles are initialized
    _build_styles()

    story: list[Any] = []

    # ── Cover page
    story += _build_cover(report_data)
    story.append(NextPageTemplate("content"))
    story.append(PageBreak())

    selskabsnavn = meta.get("selskabsnavn", "Selskab")

    # ── Content sections
    for key, builder in [
        ("sektion_01", _build_s01),
        ("sektion_02", _build_s02),
        ("sektion_03", _build_s03),
    ]:
        story += builder(report_data.get(key, {}))
        story.append(Spacer(1, 0.5 * cm))

    story += _build_s04(report_data.get("sektion_04", {}), selskabsnavn)
    story.append(Spacer(1, 0.5 * cm))

    # ── Section 04b: Påtegningshistorik (inserted between s04 and s05)
    story += _build_paategninger(report_data.get("sektion_04", {}))
    story.append(Spacer(1, 0.5 * cm))

    for key, builder in [
        ("sektion_05", _build_s05),
        ("sektion_06", _build_s06),
    ]:
        story += builder(report_data.get(key, {}))
        story.append(Spacer(1, 0.5 * cm))

    story += _build_s07(report_data.get("sektion_07", {}), selskabsnavn)
    story.append(Spacer(1, 0.5 * cm))

    for key, builder in [
        ("sektion_08", _build_s08),
        ("sektion_09", _build_s09),
    ]:
        story += builder(report_data.get(key, {}))
        story.append(Spacer(1, 0.5 * cm))

    doc.build(story)
    return buf.getvalue()
