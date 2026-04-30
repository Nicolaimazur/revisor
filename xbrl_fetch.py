"""
Finansielle nøgletal fra Erhvervsstyrelsens officielle XBRL-register.
Erstatter proff.dk-scraping (som konstant blev blokeret af Cloudflare).

Kilde:  https://distribution.virk.dk/offentliggoerelser/_search  (offentlig Elasticsearch)
Data:   Alle indleverede årsrapporter (XBRL + PDF) for danske selskaber
        med regnskabspligt. Gratis, ingen nøgle, aldrig blokeret.

Returnerer samme dict-format som det gamle proff_data for bagudkompatibilitet.
"""

import asyncio
import logging
import traceback
from typing import Optional

import httpx
from lxml import etree

log = logging.getLogger("xbrl_fetch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)


DIST_URL = "http://distribution.virk.dk/offentliggoerelser/_search"

# Mapping af XBRL element-navne (local-name, namespace-agnostisk)
# til vores interne feltnavne. Værdier er altid i DKK i XBRL — konverteres til t.DKK.
_XBRL_FIELDS = {
    # Resultatopgørelse
    "Revenue":                                     "omsaetning",
    "GrossProfit":                                 "bruttofortjeneste",
    "GrossResult":                                 "bruttofortjeneste",
    "ProfitLossFromOrdinaryOperatingActivities":   "ebit",
    "ProfitLossFromOperatingActivities":           "ebit",
    "ProfitLossFromOrdinaryActivitiesBeforeTax":   "resultat_foer_skat",
    "ProfitLoss":                                  "resultat",
    # Balance
    "Equity":                                      "egenkapital",
    "Assets":                                      "balance",
    # Øvrigt
    "AverageNumberOfEmployees":                    "ansatte",
}

_INTEGER_FIELDS = {"ansatte"}


async def fetch_xbrl_data(cvr: str, max_years: int = 5) -> dict:
    """
    Hent flerårigt regnskab for en dansk virksomhed via Erhvervsstyrelsens XBRL-register.

    Args:
        cvr: 8-cifret CVR-nummer (string eller int)
        max_years: Maks antal år at returnere (default 5)

    Returns:
        dict i samme format som det gamle proff_data:
        {
          "regnskaber": [
             {aar, omsaetning, bruttofortjeneste, resultat, egenkapital, balance, ebit},
             ...
          ],
          "ansatte":     int | None,
          "seneste_aar": "YYYY-MM",
          "kilde":       "erhvervsstyrelsen",
        }
        Returnerer tom dict {} hvis ingen data findes.
    """
    try:
        cvr_int = int(str(cvr).strip())
    except (TypeError, ValueError):
        return {}

    headers = {
        "User-Agent": "Revidera/1.0 (+https://revisor-production.up.railway.app)",
        "Accept":       "application/json; charset=utf-8",
        "Content-Type": "application/json",
    }

    try:
        # Lang timeout + tving IPv4 (distribution.virk.dk's IPv6 timer ud fra Railway)
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=2)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=30.0),
            headers=headers,
            transport=transport,
            follow_redirects=True,
        ) as client:
            # ── 1) Find seneste offentliggørelser for CVR ────────────────────
            # Kun filter på cvrNummer — offentliggoerelsestype er text-felt
            # og matcher ikke via term. Vi filtrerer i Python nedenfor.
            query = {
                "query": {"bool": {"must": [{"term": {"cvrNummer": cvr_int}}]}},
                "sort": [{"offentliggoerelsesTidspunkt": {"order": "desc"}}],
                "size": 15,  # filter på type + evt. manglende XBRL → hent rigeligt
            }
            resp = await client.post(DIST_URL, json=query)
            log.info("XBRL ES cvr=%s status=%s bytes=%s", cvr_int, resp.status_code, len(resp.content))
            if resp.status_code != 200:
                log.warning("XBRL ES non-200 body=%s", resp.text[:400])
                return {}

            hits = (resp.json().get("hits") or {}).get("hits") or []
            log.info("XBRL ES cvr=%s hits=%s", cvr_int, len(hits))
            if not hits:
                return {}

            # ── 2) Udtræk XBRL-URLs (kun fra regnskaber) ─────────────────────
            xbrl_urls = []
            for hit in hits:
                src = hit.get("_source", {}) or {}
                if (src.get("offentliggoerelsestype") or "").lower() != "regnskab":
                    continue
                for d in (src.get("dokumenter") or []):
                    mime = (d.get("dokumentMimeType") or "").lower()
                    if "xml" in mime:   # matcher "application/xml" og "text/xml"
                        url = d.get("dokumentUrl")
                        if url:
                            xbrl_urls.append(url)
                            break

            log.info("XBRL cvr=%s xbrl_urls=%s sample=%s", cvr_int, len(xbrl_urls), xbrl_urls[:2])
            if not xbrl_urls:
                return {}

            # ── 3) Hent + parse op til 5 XBRL-filer parallelt ────────────────
            tasks = [_fetch_and_parse_xbrl(client, url) for url in xbrl_urls[:5]]
            parsed_list = await asyncio.gather(*tasks, return_exceptions=True)

            # ── 4) Flet data — hvert XBRL kan indeholde flere år ────────────
            # Struktur: {"YYYY-MM": {field: value_tDKK}}
            years: dict = {}
            for parsed in parsed_list:
                if isinstance(parsed, Exception) or not parsed:
                    continue
                for aar, kpis in parsed.items():
                    if aar not in years:
                        years[aar] = {}
                    # Foretræk "rigere" data (flere felter udfyldt)
                    for k, v in kpis.items():
                        if v is not None and years[aar].get(k) in (None, 0):
                            years[aar][k] = v

            log.info("XBRL cvr=%s parsed_years=%s", cvr_int, sorted(years.keys(), reverse=True))
            if not years:
                return {}

            # ── 5) Sortér desc, tag top N ────────────────────────────────────
            sorted_years = sorted(years.items(), reverse=True)[:max_years]

            regnskaber = []
            for aar, kpis in sorted_years:
                regnskaber.append({
                    "aar":               aar,   # "YYYY-MM"
                    "omsaetning":        kpis.get("omsaetning"),
                    "bruttofortjeneste": kpis.get("bruttofortjeneste"),
                    "resultat":          kpis.get("resultat") or kpis.get("resultat_foer_skat"),
                    "egenkapital":       kpis.get("egenkapital"),
                    "balance":           kpis.get("balance"),
                    "ebit":              kpis.get("ebit"),
                })

            ansatte = sorted_years[0][1].get("ansatte") if sorted_years else None
            seneste = regnskaber[0]["aar"] if regnskaber else ""

            return {
                "regnskaber":  regnskaber,
                "ansatte":     ansatte,
                "seneste_aar": seneste,
                "kilde":       "erhvervsstyrelsen",
            }
    except Exception as e:
        log.error("XBRL fetch cvr=%s fejlede: %s\n%s", cvr, e, traceback.format_exc())
        return {}


# ── Interne hjælpefunktioner ─────────────────────────────────────────────────

async def _fetch_and_parse_xbrl(client: httpx.AsyncClient, url: str) -> dict:
    """Hent én XBRL-fil, parse, returnér {aar: {field: value_tDKK}}."""
    try:
        resp = await client.get(url, timeout=20.0)
        if resp.status_code != 200 or not resp.content:
            return {}
        return _parse_xbrl(resp.content)
    except Exception:
        return {}


def _parse_xbrl(xml_bytes: bytes) -> dict:
    """
    Parse XBRL instance-dokument.

    XBRL-filen indeholder typisk BÅDE indeværende år og sammenligningsår
    (via forskellige contextRef). Vi bygger først et context-map (id → slutdato),
    derefter scanner vi alle finansielle elementer og matcher dem til rigtigt år.

    Returnerer {"YYYY-MM": {field: value_in_tDKK}}.
    """
    try:
        # recover=True håndterer små XBRL-defekter uden at crashe
        parser = etree.XMLParser(recover=True, huge_tree=True)
        root = etree.fromstring(xml_bytes, parser=parser)
    except (etree.XMLSyntaxError, ValueError):
        return {}
    if root is None:
        return {}

    # ── A) Byg context-map: {contextId: "YYYY-MM"} ───────────────────────────
    contexts: dict = {}
    for ctx in root.iter():
        if etree.QName(ctx).localname != "context":
            continue
        cid = ctx.get("id")
        if not cid:
            continue
        # Find enten <endDate> (periode) eller <instant> (tidspunkt for balance)
        end_date = None
        for child in ctx.iter():
            lname = etree.QName(child).localname
            if lname in ("endDate", "instant"):
                txt = (child.text or "").strip()
                if len(txt) >= 7:
                    end_date = txt[:7]   # "YYYY-MM"
                    # Foretræk endDate (periodens slutning) frem for instant
                    if lname == "endDate":
                        break
        if end_date:
            contexts[cid] = end_date

    if not contexts:
        log.warning("XBRL parse: ingen contexts fundet — filen er sandsynligvis malformed eller tom")
        return {}

    # ── B) Udtræk finansielle værdier per context ────────────────────────────
    years: dict = {}   # "YYYY-MM" → {field: value}
    for elem in root.iter():
        lname = etree.QName(elem).localname
        field = _XBRL_FIELDS.get(lname)
        if not field:
            continue
        ctx_ref = elem.get("contextRef")
        if not ctx_ref or ctx_ref not in contexts:
            continue
        txt = (elem.text or "").strip()
        if not txt:
            continue
        try:
            val = float(txt)
        except ValueError:
            continue

        aar = contexts[ctx_ref]
        if aar not in years:
            years[aar] = {}

        # XBRL numeriske værdier er ALTID i DKK — konverter til t.DKK.
        # Undtagelse: ansatte (antal, ikke beløb).
        if field in _INTEGER_FIELDS:
            out = int(val)
        else:
            out = round(val / 1000.0, 1)

        # Første ikke-null værdi vinder (der kan være flere forekomster med
        # samme contextRef for forskellige dimensioner)
        existing = years[aar].get(field)
        if existing in (None, 0):
            years[aar][field] = out

    return years
