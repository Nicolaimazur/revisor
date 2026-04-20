"""
KTV-beskrivelse generator — Kunde- og Virksomhedsforståelse (ISA 315)

Genererer et dokument der ligner det revisorer allerede bruger:
- Blå baggrund = auto-udfyldt fra CVR / proff.dk / Claude
- Grå baggrund = skal udfyldes manuelt af revisor
"""

import io
from typing import Any

from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, NextPageTemplate, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

# ── Farvepalette (matcher KTV-dokumentet) ────────────────────────────────────
YELLOW_HDR  = HexColor("#f0c040")   # Gul sektionsheader
NAVY        = HexColor("#1e3050")   # Mørk header-bar
AUTO_BG     = HexColor("#ddeeff")   # Lyseblå: auto-udfyldt
MANUAL_BG   = HexColor("#e8e8e8")   # Lysegrå: skal udfyldes manuelt
MANUAL_TXT  = HexColor("#aaaaaa")   # Grå placeholder-tekst
BORDER      = HexColor("#cccccc")
WHITE       = colors.white
BLACK       = colors.black
BLUE_TXT    = HexColor("#1a4a80")   # Mørkeblå tekst i auto-felter
GRAY_TXT    = HexColor("#555555")

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm
CONTENT_W  = PAGE_W - 2 * MARGIN
COL1_W     = 5.2 * cm
COL2_W     = CONTENT_W - COL1_W

MANUAL_PLACEHOLDER = "Udfyldes af revisor"
MANUAL_LINES_1 = "\n\n"          # 1 linje tom plads
MANUAL_LINES_2 = "\n\n\n\n"      # 2 linjer tom plads
MANUAL_LINES_3 = "\n\n\n\n\n\n"  # 3 linjer tom plads


# ── Styles ────────────────────────────────────────────────────────────────────
_S: dict = {}

def _s(name: str) -> ParagraphStyle:
    if not _S:
        _build_styles()
    return _S[name]

def _build_styles():
    _S.update({
        "label": ParagraphStyle("label", fontName="Helvetica-Bold",
                                fontSize=8.5, textColor=BLACK, leading=12),
        "auto": ParagraphStyle("auto", fontName="Helvetica",
                               fontSize=8.5, textColor=BLUE_TXT, leading=12),
        "manual": ParagraphStyle("manual", fontName="Helvetica-Oblique",
                                 fontSize=8, textColor=MANUAL_TXT, leading=12),
        "sec_hdr": ParagraphStyle("sec_hdr", fontName="Helvetica-Bold",
                                  fontSize=9, textColor=BLACK, leading=13),
        "sub_hdr": ParagraphStyle("sub_hdr", fontName="Helvetica-Bold",
                                  fontSize=8.5, textColor=BLACK, leading=12),
        "konklusion": ParagraphStyle("konklusion", fontName="Helvetica-Bold",
                                     fontSize=8.5, textColor=BLUE_TXT, leading=12),
        "hdr": ParagraphStyle("hdr", fontName="Helvetica",
                              fontSize=7, textColor=WHITE, leading=9),
        "ftr": ParagraphStyle("ftr", fontName="Helvetica",
                              fontSize=7, textColor=GRAY_TXT, leading=9),
        "title": ParagraphStyle("title", fontName="Helvetica-Bold",
                                fontSize=14, textColor=BLACK, leading=18,
                                spaceAfter=6),
        "cover_sub": ParagraphStyle("cover_sub", fontName="Helvetica",
                                    fontSize=10, textColor=GRAY_TXT, leading=14),
        "idx_label": ParagraphStyle("idx_label", fontName="Helvetica",
                                    fontSize=8.5, textColor=BLACK, leading=12),
        "idx_auto": ParagraphStyle("idx_auto", fontName="Helvetica",
                                   fontSize=8.5, textColor=BLUE_TXT, leading=12),
        "idx_manual": ParagraphStyle("idx_manual", fontName="Helvetica-Oblique",
                                     fontSize=8.5, textColor=MANUAL_TXT, leading=12),
    })


# ── Page templates ────────────────────────────────────────────────────────────

def _make_templates(doc, meta: dict) -> list:
    company  = meta.get("selskabsnavn", "")
    rap_dato = meta.get("rapport_dato", "")

    def hdr_ftr(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, PAGE_H - 1.1 * cm, PAGE_W, 1.1 * cm, fill=1, stroke=0)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(WHITE)
        canvas.drawString(MARGIN, PAGE_H - 0.68 * cm, "KTV BESKRIVELSE — FORTROLIGT")
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 0.68 * cm, company)
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 1.1 * cm, PAGE_W - MARGIN, 1.1 * cm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(GRAY_TXT)
        canvas.drawString(MARGIN, 0.65 * cm, f"Udarbejdet {rap_dato}")
        canvas.drawRightString(PAGE_W - MARGIN, 0.65 * cm, f"Side {doc.page}")
        canvas.restoreState()

    frame = Frame(MARGIN, 1.5 * cm, CONTENT_W,
                  PAGE_H - 1.1 * cm - 1.5 * cm,
                  leftPadding=0, rightPadding=0,
                  topPadding=0.3 * cm, bottomPadding=0)
    return [PageTemplate(id="main", frames=[frame], onPage=hdr_ftr)]


# ── Row builders ──────────────────────────────────────────────────────────────

def _section_header(title: str) -> Table:
    """Gul sektionsheader der spænder over begge kolonner."""
    tbl = Table(
        [[Paragraph(title, _s("sec_hdr"))]],
        colWidths=[CONTENT_W],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), YELLOW_HDR),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    return tbl


def _sub_header(title: str) -> Table:
    """Hvid sub-sektionsheader i fed."""
    tbl = Table(
        [[Paragraph(title, _s("sub_hdr"))]],
        colWidths=[CONTENT_W],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), HexColor("#f5f5f5")),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("BOX",           (0, 0), (-1, -1), 0.5, BORDER),
    ]))
    return tbl


def _auto_row(label: str, content: str) -> list:
    """Række med auto-udfyldt indhold (blå baggrund)."""
    return [
        Paragraph(label, _s("label")),
        Paragraph(str(content) if content else "—", _s("auto")),
    ]


def _manual_row(label: str, lines: str = MANUAL_LINES_2) -> list:
    """Række med grå boks — skal udfyldes manuelt."""
    return [
        Paragraph(label, _s("label")),
        Paragraph(MANUAL_PLACEHOLDER + lines, _s("manual")),
    ]


def _konklusion_row(text: str = None) -> list:
    """Konklusionsrække — auto hvis tekst gives, manuel hvis None."""
    if text:
        return [Paragraph("Konklusion", _s("label")),
                Paragraph(str(text), _s("konklusion"))]
    return [Paragraph("Konklusion", _s("label")),
            Paragraph(MANUAL_PLACEHOLDER + MANUAL_LINES_1, _s("manual"))]


def _build_rows_table(rows: list, style_overrides: list = None) -> Table:
    """Byg en to-kolonne tabel fra rækker."""
    tbl = Table(rows, colWidths=[COL1_W, COL2_W])
    ts = [
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
    ]
    # Farvelæg rækker baseret på indhold
    for i, row in enumerate(rows):
        content_cell = row[1] if len(row) > 1 else None
        if content_cell and hasattr(content_cell, 'style'):
            style_name = getattr(content_cell.style, 'name', '')
            if 'manual' in style_name or 'idx_manual' in style_name:
                ts.append(("BACKGROUND", (1, i), (1, i), MANUAL_BG))
            elif 'auto' in style_name or 'konklusion' in style_name or 'idx_auto' in style_name:
                ts.append(("BACKGROUND", (1, i), (1, i), AUTO_BG))
            else:
                ts.append(("BACKGROUND", (0, i), (-1, i), WHITE))
        if style_overrides:
            ts.extend(style_overrides)
    tbl.setStyle(TableStyle(ts))
    return tbl


# ── Indeks / forside ──────────────────────────────────────────────────────────

def _build_cover(data: dict) -> list:
    meta = data.get("meta", {})
    s01  = data.get("sektion_01", {})
    s09  = data.get("sektion_09", {})

    story = []
    story.append(Spacer(1, 0.5 * cm))
    story.append(Paragraph(
        meta.get("selskabsnavn", ""),
        ParagraphStyle("ct", fontName="Helvetica-Bold", fontSize=20,
                       textColor=BLACK, leading=24, spaceAfter=2)
    ))
    story.append(Paragraph(
        f"CVR {meta.get('cvr', '')}  |  {meta.get('adresse', '')}",
        _s("cover_sub")
    ))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(
        "KTV BESKRIVELSE — Kunde- og Virksomhedsforståelse",
        ParagraphStyle("ktvt", fontName="Helvetica-Bold", fontSize=11,
                       textColor=NAVY, leading=14, spaceAfter=2)
    ))
    story.append(Paragraph(
        f"Udarbejdet den __________ af __________",
        _s("cover_sub")
    ))
    story.append(Spacer(1, 0.6 * cm))

    # Indekstabel — oversigt over konklusioner
    idx_rows = [
        [Paragraph("Fokusområde", ParagraphStyle("ih", fontName="Helvetica-Bold",
                   fontSize=8.5, textColor=WHITE, leading=11)),
         Paragraph("Konklusion / Bemærkning", ParagraphStyle("ih2", fontName="Helvetica-Bold",
                   fontSize=8.5, textColor=WHITE, leading=11))],
    ]

    # Hent konklusioner fra Claude-genererede data
    risiko = {r.get("indikator", ""): r.get("bemærkning", "")
              for r in data.get("risiko_oversigt", [])}

    index_items = [
        ("Organisations- og ejerstruktur",
         data.get("sektion_03", {}).get("revisor_anbefaling") or
         risiko.get("Kompleks koncernstruktur"), False),
        ("Ledelsesstruktur",
         None, True),
        ("Forretningsmodel",
         data.get("sektion_01", {}).get("selskabsbeskrivelse", "")[:120] + "…"
         if data.get("sektion_01", {}).get("selskabsbeskrivelse") else None, False),
        ("Økonomirapportering",
         None, True),
        ("Regnskabsmæssig begrebsramme",
         None, True),
        ("Regnskabsmæssige skøn",
         None, True),
        ("Kontrolmiljø",
         None, True),
        ("Risikovurderingsproces",
         None, True),
        ("Overvågning af internt kontrolsystem",
         None, True),
        ("Informationssystem og kommunikation",
         None, True),
        ("Kontrolaktiviteter",
         None, True),
    ]

    for i, (label, content, is_manual) in enumerate(index_items):
        bg = MANUAL_BG if is_manual else AUTO_BG
        txt_style = _s("idx_manual") if is_manual else _s("idx_auto")
        idx_rows.append([
            Paragraph(label, _s("idx_label")),
            Paragraph(str(content) if content else MANUAL_PLACEHOLDER, txt_style),
        ])

    idx_tbl = Table(idx_rows, colWidths=[COL1_W, COL2_W])
    idx_ts = [
        ("BACKGROUND",    (0, 0), (-1, 0), NAVY),
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]
    for i, (_, content, is_manual) in enumerate(index_items):
        bg = MANUAL_BG if is_manual else AUTO_BG
        idx_ts.append(("BACKGROUND", (1, i + 1), (1, i + 1), bg))
    idx_tbl.setStyle(TableStyle(idx_ts))
    story.append(idx_tbl)

    legend = Table([[
        Table([[Paragraph("  ", _s("auto"))]], colWidths=[0.55*cm],
              style=TableStyle([("BACKGROUND",(0,0),(-1,-1),AUTO_BG),("BOX",(0,0),(-1,-1),0.5,BORDER)])),
        Paragraph(" Auto-udfyldt fra CVR / proff.dk / offentlige data", _s("ftr")),
        Spacer(0.3*cm, 0),
        Table([[Paragraph("  ", _s("manual"))]], colWidths=[0.55*cm],
              style=TableStyle([("BACKGROUND",(0,0),(-1,-1),MANUAL_BG),("BOX",(0,0),(-1,-1),0.5,BORDER)])),
        Paragraph(" Skal udfyldes manuelt af revisor", _s("ftr")),
    ]], colWidths=[0.7*cm, 5.5*cm, 0.5*cm, 0.7*cm, 5.5*cm])
    legend.setStyle(TableStyle([
        ("VALIGN",        (0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",    (0,0),(-1,-1),0),
        ("BOTTOMPADDING", (0,0),(-1,-1),0),
        ("LEFTPADDING",   (0,0),(-1,-1),0),
        ("RIGHTPADDING",  (0,0),(-1,-1),0),
    ]))
    story.append(Spacer(1, 6))
    story.append(legend)
    return story


# ── Sektion 1 — Forståelse af virksomheden ───────────────────────────────────

def _build_s1_virksomhed(data: dict) -> list:
    s01 = data.get("sektion_01", {})
    s02 = data.get("sektion_02", {})
    s03 = data.get("sektion_03", {})
    s04 = data.get("sektion_04", {})
    s07 = data.get("sektion_07", {})

    story = []
    story.append(_section_header(
        "1. Forståelse af virksomheden og dens omgivelser\n"
        "Planlægningen baseres på forståelse af virksomheden og dens omgivelser. Denne forståelse "
        "skal være tilstrækkelig til at identificere og vurdere risiciene for væsentlig fejlinformation."
    ))
    story.append(Spacer(1, 4))

    # ── Organisations- og ejerstruktur ───────────────────────────────────────
    story.append(_sub_header("Organisations- og ejerstruktur:"))

    # Byg ejer-tekst fra reelle ejere
    reelle = s02.get("reelle_ejere") or []
    ejer_txt = "; ".join(
        f"{e.get('navn','')} ({e.get('ejerandel','')})"
        for e in reelle if e.get('navn')
    ) or None

    rows = [
        _auto_row("Organisationsstruktur", s03.get("beskrivelse")),
        _auto_row("Ejerforhold", ejer_txt),
        _manual_row("Nærtstående parter"),
        _manual_row("Nærtstående parter"),
        _manual_row("Nærtstående parter"),
        _konklusion_row(s03.get("revisor_anbefaling")),
    ]
    story.append(_build_rows_table(rows))
    story.append(Spacer(1, 4))

    # ── Ledelsesstruktur ─────────────────────────────────────────────────────
    story.append(_sub_header("Ledelsesstruktur:"))

    direktion = s02.get("direktion") or []
    best_txt = "; ".join(
        d.get("navn", "") for d in direktion
        if "Bestyrelsesmedlem" in d.get("rolle", "") or "Bestyrelsesformand" in d.get("rolle", "")
    ) or None
    dir_txt = "; ".join(
        f"{d.get('navn','')} ({d.get('rolle','')})" for d in direktion
        if "Direktør" in d.get("rolle", "") or "Adm." in d.get("rolle", "")
    ) or None
    if not dir_txt and direktion:
        dir_txt = "; ".join(f"{d.get('navn','')} ({d.get('rolle','')})" for d in direktion)

    rows = [
        _auto_row("Bestyrelse",             best_txt or dir_txt),
        _auto_row("Direktion/daglig ledelse", dir_txt),
        _manual_row("Nøglepersoner"),
        _konklusion_row(),
    ]
    story.append(_build_rows_table(rows))
    story.append(Spacer(1, 4))

    # ── Forretningsmodel ─────────────────────────────────────────────────────
    story.append(_sub_header("Forretningsmodel:"))

    # Finansiering fra risiko-oversigt eller tekst
    finans_hint = next(
        (r.get("bemærkning","") for r in data.get("risiko_oversigt",[])
         if "finansiel" in r.get("indikator","").lower()), None
    )

    rows = [
        _auto_row("Aktivitet / drift / mål", s01.get("selskabsbeskrivelse")),
        _auto_row("Branche",                 s07.get("markedssituation") or s01.get("branchekode")),
        _auto_row("Produkter / Ydelser",     s01.get("formaal")),
        _manual_row("Omsætning"),
        _manual_row("Kunder"),
        _manual_row("Leverandører"),
        _auto_row("Lovgivning",              "Hvidvaskloven: " + ("Ja" if data.get("hvidvask_omfattet") else "Nej — ikke direkte omfattet")),
        _manual_row("Finansiering"),
        _manual_row("Investeringer"),
        _manual_row("Integration af IT i forretningsmodellen"),
        _konklusion_row(),
    ]
    story.append(_build_rows_table(rows))
    story.append(Spacer(1, 4))

    # ── Økonomirapportering ──────────────────────────────────────────────────
    story.append(_sub_header("Økonomirapportering:"))

    aar = (s04.get("aar_kolonner") or [None])[0]
    fin_txt = None
    if aar:
        reg = s04.get("regnskabspost_tabel") or []
        parts = []
        for r in reg[:3]:
            vals = r.get("vaerdier") or []
            if vals and vals[0]:
                parts.append(f"{r.get('post','')} {vals[0]}")
        if parts:
            fin_txt = f"Seneste regnskabsår {aar}: " + ", ".join(parts)

    rows = [
        _auto_row("Økonomirapportering",
                  fin_txt or s04.get("finansiel_analyse")),
        _manual_row("Økonomirapportering (interne processer)", MANUAL_LINES_3),
        _konklusion_row(),
    ]
    story.append(_build_rows_table(rows))
    story.append(Spacer(1, 4))

    # ── Regnskabsmæssig begrebsramme ─────────────────────────────────────────
    story.append(_sub_header("Regnskabsmæssig begrebsramme:"))
    rows = [
        _manual_row("Regnskabsmæssig begrebsramme"),
        _manual_row("Omsætning, indregningskriterium"),
        _manual_row("Omsætning, leveringsbetingelser"),
        _manual_row("Særlige forhold vedr. regnskabspraksis"),
        _manual_row("Nye regnskabsregler"),
        _manual_row("Ændring i regnskabspraksis"),
        _manual_row("Ændring i aktiviteter"),
        _manual_row("Usædvanlige/komplekse transaktioner"),
        _konklusion_row(),
    ]
    story.append(_build_rows_table(rows))
    story.append(Spacer(1, 4))

    # ── Regnskabsmæssige skøn ────────────────────────────────────────────────
    story.append(_sub_header("Regnskabsmæssige skøn:"))
    rows = [
        _manual_row("Udøvelse heraf", MANUAL_LINES_2),
        _manual_row("Goodwill"),
        _manual_row("Investeringsejendomme"),
        _manual_row("Varebeholdninger"),
        _manual_row("Tilgodehavender fra salg"),
        _manual_row("Igangværende arbejder"),
        _manual_row("Skatteaktiv"),
        _konklusion_row(),
    ]
    story.append(_build_rows_table(rows))
    return story


# ── Sektion 2-6 — Kontrolafsnit ──────────────────────────────────────────────

def _build_kontrolafsnit(data: dict) -> list:
    story = []

    sektioner = [
        ("2. Kontrolmiljø",
         "Vi skal opnå en forståelse af det kontrolmiljø, der er relevant for regnskabsaflæggelsen.",
         ["Daglig ledelse", "Øverste ledelse", "Bemyndigelse og ansvar",
          "Medarbejdere", "Ansvarsområder", "Fritekst"]),
        ("3. Risikovurderingsproces",
         "Vi skal opnå en forståelse af virksomhedens risikovurderingsprocesser, "
         "der er relevante for regnskabsaflæggelsen.",
         ["Identifikation af risici", "Vurdering af risici",
          "Afdækning af risici", "Fritekst"]),
        ("4. Overvågning af det interne kontrolsystem",
         "Vi skal opnå en forståelse af virksomhedens proces for overvågning af det interne "
         "kontrolsystem, der er relevante for regnskabsaflæggelsen.",
         ["Vurdering af kontrolsystem", "Udbedring af mangler",
          "Informationskilder", "Fritekst"]),
        ("5. Informationssystem og kommunikation",
         "Vi skal opnå en forståelse af virksomhedens informationssystem og kommunikation, "
         "der er relevante for regnskabsaflæggelsen.",
         ["Transaktioner", "Fejlrettelser", "Øvrige informationer",
          "Bogføringsmateriale", "Regnskabsaflæggelsesproces",
          "Ressourcer", "Kommunikation", "Fritekst"]),
        ("6. Kontrolaktiviteter",
         "Vi skal opnå en forståelse af virksomhedens kontrolaktiviteter.",
         ["Finansposteringer", "IT-kontroller", "Fritekst"]),
    ]

    for titel, intro, felter in sektioner:
        story.append(Spacer(1, 6))
        story.append(_section_header(f"{titel}\n{intro}"))
        story.append(Spacer(1, 4))
        rows = [_manual_row(felt, MANUAL_LINES_2) for felt in felter]
        rows.append(_konklusion_row())
        story.append(_build_rows_table(rows))

    return story


# ── Hoved-entry ───────────────────────────────────────────────────────────────

def generate_ktv(report_data: dict) -> bytes:
    """Genererer KTV-beskrivelse som PDF-bytes."""
    buf = io.BytesIO()
    meta = report_data.get("meta", {})
    _build_styles()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=0, rightMargin=0,
        topMargin=0, bottomMargin=0,
    )
    doc.addPageTemplates(_make_templates(doc, meta))

    story: list[Any] = []

    # Forside / indeks
    story += _build_cover(report_data)
    story.append(PageBreak())

    # Sektion 1 — Virksomhedsforståelse
    story += _build_s1_virksomhed(report_data)
    story.append(PageBreak())

    # Sektioner 2-6 — Kontrolafsnit
    story += _build_kontrolafsnit(report_data)

    # Afsluttende signatur-felt
    story.append(Spacer(1, 0.8 * cm))
    sig_tbl = Table(
        [[Paragraph("Dato / SR:", _s("label")),
          Paragraph(" " * 40, _s("manual"))]],
        colWidths=[COL1_W, COL2_W],
    )
    sig_tbl.setStyle(TableStyle([
        ("GRID",          (0, 0), (-1, -1), 0.5, BORDER),
        ("TOPPADDING",    (0, 0), (-1, -1), 18),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 18),
        ("LEFTPADDING",   (0, 0), (-1, -1), 6),
        ("BACKGROUND",    (1, 0), (1, 0), MANUAL_BG),
    ]))
    story.append(sig_tbl)

    doc.build(story)
    return buf.getvalue()
