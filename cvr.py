"""
CVR data fetching via Playwright.
Uses datacvr.virk.dk's gateway API (same data the website shows publicly)
through a headless browser to bypass Cloudflare bot protection.
"""

import asyncio
import logging
import re
import json
from typing import Optional

log = logging.getLogger("cvr_fetch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)
from urllib.parse import quote as _url_quote
from playwright.async_api import async_playwright

from xbrl_fetch import fetch_xbrl_data
from tinglysning_fetch import fetch_tinglysning_data

GATEWAY_URL = (
    "https://datacvr.virk.dk/gateway/virksomhed/hentVirksomhed"
    "?cvrnummer={cvr}&locale=da"
)

NOEGLETAL_URL = (
    "https://datacvr.virk.dk/gateway/regnskab/hentRegnskabsNoegletal"
    "?cvrnummer={cvr}&sideIndex=0&size=5&locale=da"
)

REGNSKAB_LISTE_URL = (
    "https://datacvr.virk.dk/gateway/regnskab/hentRegnskabListe"
    "?cvrnummer={cvr}&sideIndex=0&size=5&locale=da"
)

PROFF_HOME = "https://www.proff.dk/"
PROFF_SEARCH_URL = "https://www.proff.dk/_next/data/{build_id}/search.json?q={cvr}"


async def _fetch_json(page, url: str, timeout_ms: int = 20000):
    """Fetch JSON via browser fetch() — returns None on error.

    Bruger AbortController så kaldet aldrig hænger uendeligt, selv hvis den
    eksterne tjeneste er langsom eller svarer ikke.
    """
    return await page.evaluate(f"""
        async () => {{
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), {timeout_ms});
            try {{
                const r = await fetch('{url}', {{ signal: ctrl.signal }});
                clearTimeout(timer);
                if (!r.ok) return null;
                return await r.json();
            }} catch(e) {{
                clearTimeout(timer);
                return null;
            }}
        }}
    """)


async def _resolve_owner_cvr(page, enhed_id: str) -> Optional[str]:
    """
    Navigate to the owner's virk.dk page and extract its CVR number
    from the page text (8-digit number).  Returns None if not found.
    """
    try:
        await page.goto(
            f"https://datacvr.virk.dk/enhed/virksomhed/{enhed_id}",
            wait_until="networkidle",
            timeout=20000,
        )
        text = await page.inner_text("body")
        hits = re.findall(r'\b(\d{8})\b', text)
        # Filter to plausible CVR range
        hits = [h for h in hits if 10_000_000 <= int(h) <= 99_999_999]
        return hits[0] if hits else None
    except Exception:
        return None


PERSON_URL = (
    "https://datacvr.virk.dk/gateway/person/hentPerson"
    "?enhedsnummer={enhed_id}&persontype=deltager&locale=da"
)

_KONKURS_KEYWORDS = ["KONKURS", "TVANGS", "LIKVIDATION"]


async def _fetch_person_risiko(page, enhed_id: str, navn: str) -> dict:
    """
    Fetch a person's company history and flag any that went bankrupt.
    Returns a dict with the person's name and list of bankrupt/dissolved companies.
    """
    data = await _fetch_json(page, PERSON_URL.format(enhed_id=enhed_id))
    if not data:
        log.warning("person_risiko %s (%s): ingen data fra person-API", navn, enhed_id)
        return {"navn": navn, "enhed_id": enhed_id, "konkurser": [], "aktive_selskaber": []}

    # Debug: log top-level keys so we can see the actual structure
    top_keys = list(data.keys()) if isinstance(data, dict) else []
    log.info("person_risiko %s: API top-keys=%s", navn, top_keys)

    relationer = data.get("personRelationer", {}) or {}
    rel_keys = list(relationer.keys()) if isinstance(relationer, dict) else []
    log.info("person_risiko %s: personRelationer keys=%s", navn, rel_keys)

    aktive = relationer.get("aktiveRelationer", []) or []
    ophoerte = relationer.get("ophoerteRelationer", []) or []
    log.info("person_risiko %s: aktive=%d ophoerte=%d", navn, len(aktive), len(ophoerte))

    rolle_map = {
        "adm_dir":           "Adm. direktør",
        "direktoerer":       "Direktør",
        "direktion":         "Direktør",
        "bestyrelsesmedlemmer": "Bestyrelsesmedlem",
        "stiftere":          "Stifter",
        "legale_ejere":      "Legal ejer",
        "fuldt_ansvarlig_deltagere": "Fuldt ansvarlig deltager",
    }

    def _rolle(r):
        key = r.get("tekstnogle", "").replace("erstdist-organisation-rolle-", "")
        return rolle_map.get(key, key)

    # Collect unique bankrupt/dissolved companies.
    # Person-API returnerer ofte tom virksomhedsstatus — opslag på selskabets CVR
    # er nødvendigt for at få den faktiske status (TVANGSOPLØST, KONKURS etc.)
    seen_cvr: set = set()
    unikke_ophoerte = []
    for rel in ophoerte:
        cvr_nr = rel.get("cvrnummer", "")
        if cvr_nr and cvr_nr not in seen_cvr:
            seen_cvr.add(cvr_nr)
            unikke_ophoerte.append(rel)
        elif not cvr_nr:
            log.warning("person_risiko %s: ophørt relation mangler cvrnummer: %s", navn, rel)

    log.info("person_risiko %s: unikke ophørte CVR'er=%d: %s",
             navn, len(unikke_ophoerte), [r.get("cvrnummer") for r in unikke_ophoerte])

    # Hent status parallelt for alle ophørte selskaber (maks 20)
    async def _hent_status(rel):
        status = rel.get("virksomhedsstatus") or ""
        cvr_nr = rel.get("cvrnummer", "")
        selskab_navn = rel.get("senesteNavn", "")
        log.info("person_risiko %s: checker CVR %s (%s) — inline_status=%r",
                 navn, cvr_nr, selskab_navn, status)
        if not status and cvr_nr:
            try:
                co_data = await _fetch_json(page, GATEWAY_URL.format(cvr=cvr_nr), timeout_ms=8000)
                if co_data:
                    status = (co_data.get("stamdata", {}) or {}).get("status", "") or ""
                    log.info("person_risiko %s: CVR %s (%s) → status=%r",
                             navn, cvr_nr, selskab_navn, status)
                else:
                    log.warning("person_risiko %s: CVR-opslag returnerede ingen data for %s (%s)",
                                navn, cvr_nr, selskab_navn)
            except Exception as e:
                log.warning("person_risiko %s: CVR-opslag fejlede for %s (%s): %s",
                            navn, cvr_nr, selskab_navn, e)
        return rel, status

    resultater = await asyncio.gather(
        *[_hent_status(r) for r in unikke_ophoerte[:20]],
        return_exceptions=True,
    )

    konkurser = []
    timeout_count = 0
    for res in resultater:
        if isinstance(res, Exception):
            timeout_count += 1
            log.warning("person_risiko %s: opslag kastede exception: %s", navn, res)
            continue
        rel, status = res
        if any(k in status.upper() for k in _KONKURS_KEYWORDS):
            konkurser.append({
                "cvr":    rel.get("cvrnummer", ""),
                "navn":   rel.get("senesteNavn", ""),
                "status": status,
                "rolle":  _rolle(rel),
            })

    if timeout_count:
        log.warning("person_risiko %s: %d/%d selskabsopslag fejlede — konkurshistorik kan være ufuldstændig",
                    navn, timeout_count, len(unikke_ophoerte))

    log.info("person_risiko %s: fandt %d konkurs/tvangs/likvidations-selskaber: %s",
             navn, len(konkurser), [k.get("navn") for k in konkurser])

    # Aktive selskaber (summary)
    aktive_unikke = {}
    for rel in aktive:
        cvr_nr = rel.get("cvrnummer", "")
        if cvr_nr not in aktive_unikke:
            aktive_unikke[cvr_nr] = rel.get("senesteNavn", "")

    return {
        "navn":             navn,
        "enhed_id":         enhed_id,
        "aktive_selskaber": list(aktive_unikke.values()),
        "konkurser":        konkurser,   # list of companies gone bankrupt with this person
    }


async def _fetch_owner_risiko(page, cvr: str) -> dict:
    """
    Fetch status + konkurshistorik + registreringer for an owner company.
    Returns a dict with risk summary.
    """
    data = await _fetch_json(page, GATEWAY_URL.format(cvr=cvr))
    if not data:
        return {"cvr": cvr, "status": "ukendt", "konkurs_historik": [], "registreringer": []}

    stam = data.get("stamdata", {}) or {}
    hist = (data.get("historiskStamdata", {}) or {}).get("status", []) or []

    konkurs = [
        s for s in hist
        if any(k in str(s).upper() for k in ["KONKURS", "TVANGS", "LIKVIDATION", "OPHOERT"])
    ]

    regs_raw = data.get("virksomhedRegistreringer") or []
    regs = []
    for reg in regs_raw[:10]:
        titler = reg.get("titelTekstnogler") or []
        # Only include registrations relevant to risk
        if any(t for t in titler if any(k in t for k in
               ["konkurs", "ophoer", "kapital", "omdannelse", "tvangs", "personkreds"])):
            tekst_node = reg.get("registreringsTekst") or {}
            tekst = tekst_node.get("tekstUdenLink", "") or ""
            tekst_ren = re.sub(r"<[^>]+>", " ", tekst).strip()
            tekst_ren = " ".join(tekst_ren.split())[:200]
            regs.append({
                "dato":   reg.get("offentliggoerelseTidsstempel", ""),
                "titler": titler,
                "tekst":  tekst_ren,
            })

    return {
        "cvr":             cvr,
        "navn":            stam.get("navn", ""),
        "status":          stam.get("status", "UKENDT"),
        "stiftelsesdato":  stam.get("startdato", ""),
        "selskabsform":    stam.get("virksomhedsform", ""),
        "adresse":         f"{stam.get('adresse', '')}, {stam.get('postnummerOgBy', '')}",
        "konkurs_historik": konkurs,
        "registreringer":  regs,
    }


def _proff_slug(text: str) -> str:
    """Konverter tekst til proff.dk URL-slug (beholder ø/æ/å, fjerner /)."""
    text = text.lower().replace("/", "").replace(" ", "-").replace(",", "")
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def _parse_proff_amount(text: str):
    """Parse '5.421' → 5421 (int, t.DKK). Returnerer None ved '-' eller tom."""
    text = text.strip()
    if not text or text == "-":
        return None
    cleaned = text.replace(".", "").replace(",", "").replace(" ", "")
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_proff_table(text: str) -> dict:
    """
    Parser proff.dk's regnskabstabel fra DOM-tekst.

    Springer chart-sektionen over ved at finde REGNSKABSPERIODE-markøren,
    og parser den tab-separerede tabel der følger.

    Tekstformat (tab-separeret, efter markøren):
      2020-12\t2021-12\t...
      Bruttofortjeneste\t3.610\t3.686\t...

    Returnerer {år: {felt: værdi_i_tDKK, ...}, ...}
    """
    # Spring chart-sektionen over — find selve tabellen
    marker = "REGNSKABSPERIODE"
    idx = text.find(marker)
    if idx == -1:
        return {}
    table_text = text[idx + len(marker):]

    rows_of_interest = {
        "Bruttofortjeneste":       "bruttofortjeneste",
        "Årets resultat":           "resultat",
        "Egenkapital i alt":        "egenkapital",
        "Status balance":           "balance",
        "Primært resultat (EBIT)":  "ebit",
        "Resultat før skat":        "resultat_foer_skat",
    }

    # Find header-rækken med årskolonner: "2020-12\t2021-12\t..."
    year_match = re.search(r'((?:\d{4}-\d{2}\t)+)', table_text)
    if not year_match:
        return {}
    years = [y for y in year_match.group(1).split('\t') if y.strip()]
    if not years:
        return {}

    result = {yr: {} for yr in years}

    for label, field in rows_of_interest.items():
        # Match label efterfulgt af tab-separerede værdier på samme linje
        m = re.search(re.escape(label) + r'\t([^\n]+)', table_text, re.IGNORECASE)
        if not m:
            continue
        vals = [v.strip() for v in m.group(1).split('\t') if v.strip()]
        for i, yr in enumerate(years):
            if i < len(vals):
                result[yr][field] = _parse_proff_amount(vals[i])

    return result  # {år: {bruttofortjeneste, resultat, egenkapital, balance, ...}}


async def _fetch_proff_data(page, cvr: str) -> dict:
    """
    Henter flerårigt regnskab fra proff.dk's /regnskab/-side (DOM-scraping).
    Returnerer {regnskaber: [{aar, bruttofortjeneste, resultat, egenkapital, balance}, ...],
                ansatte, seneste_aar}
    Returnerer {} ved fejl — virk.dk fallback bruges så i stedet.
    """
    try:
        # ── Trin 1: Byg slug-URL via search-API ─────────────────────────────
        try:
            await page.goto(PROFF_HOME, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            return {}  # proff.dk ikke tilgængelig — brug virk.dk fallback

        next_data_raw = await page.evaluate(
            "() => document.getElementById('__NEXT_DATA__')?.textContent"
        )
        if not next_data_raw:
            return {}
        build_id = json.loads(next_data_raw).get("buildId", "")
        if not build_id:
            return {}

        search_url = PROFF_SEARCH_URL.format(build_id=build_id, cvr=cvr)
        result = await page.evaluate(f"""
            async () => {{
                try {{
                    const r = await fetch('{search_url}', {{headers: {{'x-nextjs-data': '1'}}}});
                    if (!r.ok) return null;
                    return await r.json();
                }} catch(e) {{ return null; }}
            }}
        """)
        if not result:
            return {}

        companies = (
            result.get("pageProps", {})
            .get("hydrationData", {})
            .get("searchStore", {})
            .get("companies", {})
            .get("companies", [])
        )
        if not companies:
            return {}

        match = next((c for c in companies if str(c.get("orgnr", "")) == str(cvr)), None)
        if not match:
            match = companies[0]

        company_id    = match.get("companyId", "")
        name_slug     = _proff_slug(match.get("legalName") or match.get("name", ""))
        city_slug     = _proff_slug((match.get("visitorAddress") or {}).get("postPlace", ""))
        industry_slug = _proff_slug((match.get("currentIndustry") or {}).get("name", ""))
        ansatte       = match.get("employees")

        if not company_id:
            return {}

        # ── Trin 2: Naviger til /regnskab/-siden ─────────────────────────────
        regnskab_url = (
            f"https://www.proff.dk/regnskab"
            f"/{name_slug}/{city_slug}/{industry_slug}/{company_id}"
        )
        await page.goto(regnskab_url, wait_until="networkidle", timeout=25000)
        await page.wait_for_timeout(3000)
        await page.evaluate("window.scrollTo(0, 400)")
        await page.wait_for_timeout(2000)

        text = await page.inner_text("body")

        # ── Trin 3: Parse flerårigt regnskab ─────────────────────────────────
        tabel = _parse_proff_table(text)
        if not tabel:
            return {}

        years_sorted = sorted(tabel.keys(), reverse=True)  # seneste først
        regnskaber = []
        for yr in years_sorted:
            vals = tabel[yr]
            if any(v is not None for v in vals.values()):
                regnskaber.append({
                    "aar":              yr,
                    "bruttofortjeneste": vals.get("bruttofortjeneste"),
                    "resultat":          vals.get("resultat"),
                    "egenkapital":       vals.get("egenkapital"),
                    "balance":           vals.get("balance"),
                    "ebit":              vals.get("ebit"),
                })

        return {
            "regnskaber":  regnskaber,       # Liste, seneste år først
            "ansatte":     ansatte,
            "seneste_aar": years_sorted[0] if years_sorted else "",
        }

    except Exception:
        return {}


_LEGAL_SUFFIXES = r'\b(aps|a/s|as|ivs|is|k/s|ks|p/s|ps|fmba|smba|fond|holding|group|gmbh|llc|ltd|inc|co)\b'


def _clean_for_search(name: str) -> str:
    """
    Renser firmanavn til søgebrug:
    - Fjerner juridiske suffixes (ApS, A/S, …)
    - Normaliserer & → og
    - Fjerner overflødige tegn og mellemrum
    Resultatet bruges som søgestreng til Trustpilot.
    """
    s = name.lower().strip()
    s = re.sub(r'\s*&\s*', ' og ', s)          # & → og
    s = re.sub(_LEGAL_SUFFIXES, '', s)          # fjern suffixes
    s = re.sub(r'[^\wæøå\s]', ' ', s)          # tegnsætning → mellemrum
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _name_similarity(a: str, b: str) -> float:
    """
    Token-overlap score mellem to firmanavne (0.0–1.0).
    Håndterer:
    - Juridiske suffixes (ApS, A/S, …) ignoreres
    - & normaliseres til 'og'
    - Containment: hvis det ene navn er delmængde af det andet → høj score
      (fx "Åben" ⊂ "Åben Digital" giver 0.9 i stedet for 0.5)
    """
    def tokenize(s):
        s = _clean_for_search(s)
        return set(re.findall(r'[a-zæøå0-9]+', s))

    tokens_a = tokenize(a)
    tokens_b = tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0

    overlap = tokens_a & tokens_b
    jaccard  = len(overlap) / max(len(tokens_a), len(tokens_b))

    # Containment-bonus: det korteste navn er fuldt indeholdt i det længste
    if tokens_a <= tokens_b or tokens_b <= tokens_a:
        containment = len(overlap) / min(len(tokens_a), len(tokens_b))
        return max(jaccard, containment * 0.9)

    return jaccard


async def _scrape_trustpilot_page(page, tp_url: str) -> dict:
    """
    Henter rating, antal og anmeldelser fra en Trustpilot-virksomhedsside.

    Primær kilde: JSON-LD structured data (stabilt, ændres ikke med CSS-klasser).
    Fallback:     DOM-selektorer (bruges hvis JSON-LD mangler dele).
    """
    await page.goto(tp_url, wait_until="domcontentloaded", timeout=20000)
    await page.wait_for_timeout(2000)

    tp_name = ""
    rating  = None
    antal   = None
    reviews = []

    # ── 1. JSON-LD (primær — meget mere stabilt end CSS-klasser) ──────────
    try:
        ld_blocks = await page.evaluate("""
            () => Array.from(
                document.querySelectorAll('script[type="application/ld+json"]')
            ).map(s => { try { return JSON.parse(s.textContent); } catch(e) { return null; } })
             .filter(Boolean)
        """)
        for block in (ld_blocks or []):
            # Håndter både enkelt-objekt og @graph-array
            items = block.get("@graph", [block])
            for item in items:
                t = item.get("@type", "")
                # Virksomhedsnavn
                if not tp_name and t in ("LocalBusiness", "Organization", "Store"):
                    tp_name = item.get("name", "")

                # AggregateRating
                agg = item.get("aggregateRating") or {}
                if not rating and agg.get("ratingValue"):
                    rating = str(agg["ratingValue"])
                if not antal and agg.get("reviewCount"):
                    antal  = str(agg["reviewCount"])

                # Anmeldelser
                for rev in item.get("review", [])[:5]:
                    body   = (rev.get("reviewBody") or "")[:300]
                    titel  = (rev.get("name") or "")
                    dato   = (rev.get("datePublished") or "")[:10]
                    stjerner = None
                    rev_rating = rev.get("reviewRating") or {}
                    if rev_rating.get("ratingValue"):
                        try:
                            stjerner = int(float(rev_rating["ratingValue"]))
                        except (ValueError, TypeError):
                            pass
                    if titel or body:
                        reviews.append({
                            "stjerner": stjerner,
                            "titel":    titel,
                            "tekst":    body,
                            "dato":     dato,
                        })
    except Exception:
        pass

    # ── 2. DOM-fallback for felter JSON-LD ikke dækkede ──────────────────
    # Virksomhedsnavn
    if not tp_name:
        for sel in ['h1', '[class*="businessUnitDisplayName"]', '[data-business-unit-display-name]']:
            el = await page.query_selector(sel)
            if el:
                tp_name = (await el.inner_text()).strip()
                if tp_name:
                    break

    # Rating
    if not rating:
        for sel in ['[data-rating-typography]', '[class*="styles_rating"]', '[class*="ratingText"]']:
            el = await page.query_selector(sel)
            if el:
                m = re.search(r'(\d[,\.]\d|\d)', await el.inner_text())
                if m:
                    rating = m.group(1).replace(',', '.')
                    break

    # Antal anmeldelser
    if not antal:
        for sel in ['[data-reviews-count-typography]', '[class*="reviewsCount"]', '[class*="ratingFilters"]']:
            el = await page.query_selector(sel)
            if el:
                m = re.search(r'(\d[\d\.,]*)', (await el.inner_text()).replace('.', '').replace(',', ''))
                if m:
                    antal = m.group(1)
                    break

    # Anmeldelser (DOM-fallback hvis JSON-LD var tom)
    if not reviews:
        cards = await page.query_selector_all(
            'article[class*="review"], div[class*="reviewCard"], section[class*="review"]'
        )
        for card in cards[:5]:
            try:
                stjerner = None
                for sel in ['[class*="starRating"] img', 'img[alt*="star"]', '[data-service-review-rating]']:
                    el = await card.query_selector(sel)
                    if el:
                        alt = await el.get_attribute("alt") or await el.get_attribute("data-service-review-rating") or ""
                        m = re.search(r'(\d)', alt)
                        if m:
                            stjerner = int(m.group(1))
                        break
                titel_el = await card.query_selector('h2, [class*="title_"], [class*="reviewTitle"]')
                titel = (await titel_el.inner_text()).strip() if titel_el else ""
                body_el = await card.query_selector('p[class*="typography"], [class*="reviewContent"], [class*="reviewBody"]')
                body = (await body_el.inner_text()).strip()[:300] if body_el else ""
                dato_el = await card.query_selector('time')
                dato = ""
                if dato_el:
                    dato = (await dato_el.get_attribute("datetime") or await dato_el.inner_text() or "")[:10]
                if titel or body:
                    reviews.append({"stjerner": stjerner, "titel": titel, "tekst": body, "dato": dato})
            except Exception:
                continue

    return {"tp_name": tp_name, "rating": rating, "antal": antal, "reviews": reviews}


async def _fetch_trustpilot(page, company_name: str, website: str = "", city: str = "") -> dict:
    """
    Finder virksomhedens Trustpilot-profil med verifikation.

    Strategi (i prioriteret rækkefølge):
    1. Direkte domæne-opslag hvis CVR har en hjemmeside (sikreste)
    2. Søg på navn og verificer at Trustpilot-siden matcher CVR-virksomheden
       — afvis resultatet hvis name-similarity < 0.4

    Returnerer {} hvis ingen sikker match kan verificeres.
    """
    if not company_name:
        return {}

    async def _build_result(tp_url, data):
        if not data.get("rating") and not data.get("reviews"):
            return {}
        return {
            "url":    tp_url,
            "rating": data["rating"],
            "antal":  data["antal"],
            "reviews": data["reviews"],
        }

    try:
        # ── Strategi 1: direkte domæne fra CVR ────────────────────────────
        if website:
            domain = re.sub(r'^https?://(www\.)?', '', website.strip().rstrip('/'))
            domain = domain.split('/')[0]
            if domain:
                tp_url = f"https://www.trustpilot.com/review/{domain}"
                try:
                    data = await _scrape_trustpilot_page(page, tp_url)
                    result = await _build_result(tp_url, data)
                    if result:
                        result["match_metode"] = "domæne"
                        return result
                except Exception:
                    pass

        # ── Strategi 2: søg på renset navn (uden ApS/A/S/…) ─────────────────
        # Søg med renset navn — "Åben ApS" → søger "åben" (langt bedre træffere)
        search_name = _clean_for_search(company_name)
        if not search_name:
            return {}

        MIN_SCORE = 0.50   # Kræv mindst 50% token-overlap for at undgå falske match

        for attempt_name in [search_name, search_name.split()[0] if ' ' in search_name else None]:
            # Anden forsøg: prøv kun første signifikante ord (fx "åben digital" → "åben")
            if attempt_name is None:
                continue

            search_url = f"https://www.trustpilot.com/search?query={_url_quote(attempt_name)}&country=dk"
            try:
                await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1500)
            except Exception:
                continue

            result_links = await page.query_selector_all('a[href^="/review/"]')
            if not result_links:
                continue

            # Byg kandidatliste
            candidates = []
            for link in result_links[:8]:
                href = await link.get_attribute("href")
                if not href or "/review/" not in href:
                    continue
                parent = await link.evaluate_handle(
                    "el => el.closest('[class*=\"businessUnit\"], [class*=\"result\"], li, article')"
                )
                tp_candidate_name = ""
                try:
                    name_el = await parent.query_selector(
                        'p, h2, h3, [class*="displayName"], [class*="businessName"], [class*="title"]'
                    )
                    if name_el:
                        tp_candidate_name = (await name_el.inner_text()).strip()
                except Exception:
                    pass
                # Domænet fra href som ekstra hint (fx /review/aaben.dk → "aaben")
                domain_hint = href.replace("/review/", "").split(".")[0].replace("-", " ")
                candidates.append({
                    "href":    href,
                    "tp_name": tp_candidate_name or domain_hint,
                })

            if not candidates:
                continue

            # Score alle kandidater
            best = None
            best_score = 0.0
            for c in candidates:
                score = _name_similarity(company_name, c["tp_name"])
                if score > best_score:
                    best_score = score
                    best = c

            if best_score < MIN_SCORE or not best:
                continue   # prøv næste søgestrategi

            tp_url = f"https://www.trustpilot.com{best['href']}"
            data = await _scrape_trustpilot_page(page, tp_url)

            # Dobbelttjek navn på den faktiske side
            if data.get("tp_name"):
                final_score = _name_similarity(company_name, data["tp_name"])
                if final_score < MIN_SCORE:
                    continue  # siden hedder noget helt andet — prøv igen

            result = await _build_result(tp_url, data)
            if result:
                result["match_metode"] = "navn"
                result["match_score"]  = round(best_score, 2)
            return result

        return {}  # ingen strategi fandt sikker match

    except Exception:
        return {}


# ── Feature 1: PEP & Sanktionsliste-screening (OpenSanctions) ────────────────

async def _fetch_sanctions(page, names: list) -> list:
    """
    Screener en liste af navne mod OpenSanctions (PEP + sanktionslister).
    Bruger browserens fetch() til at kalde OpenSanctions free API.
    Returnerer liste med ét element per navn.
    """
    SCORE_THRESHOLD = 0.72  # Kun matches med høj confidence

    KNOWN_SANCTION_DATASETS = {
        "us_ofac_sdn", "eu_fsf", "un_sc_sanctions", "gb_hmt_sanctions",
        "ch_seco_sanctions", "au_dfat_sanctions", "ca_osfi_sanctions",
        "ru_nsd_isin", "us_bis_denied",
    }

    results = []
    for name in names:
        if not name:
            continue
        try:
            url = f"https://api.opensanctions.org/search/default?q={_url_quote(name)}&schema=Person&limit=5"
            data = await _fetch_json(page, url)

            if not data or not isinstance(data.get("results"), list):
                results.append({
                    "navn": name, "screenet": False,
                    "pep": False, "sanktioner": False, "fund": [],
                })
                continue

            fund = []
            for r in data["results"]:
                score = float(r.get("score") or 0.0)
                if score < SCORE_THRESHOLD:
                    continue
                datasets = r.get("datasets") or []
                er_pep        = any("pep" in d.lower() for d in datasets)
                er_sanktion   = bool(KNOWN_SANCTION_DATASETS & set(datasets))
                land_liste    = (r.get("properties") or {}).get("country") or []
                fund.append({
                    "match_navn": r.get("caption", ""),
                    "score":      round(score, 2),
                    "pep":        er_pep,
                    "sanktioner": er_sanktion,
                    "lande":      land_liste[:3],
                    "datasets":   datasets[:6],
                })

            er_pep      = any(f["pep"]        for f in fund)
            er_sanktion = any(f["sanktioner"]  for f in fund)
            results.append({
                "navn":      name,
                "screenet":  True,
                "pep":       er_pep,
                "sanktioner": er_sanktion,
                "fund":      fund,
            })
        except Exception:
            results.append({
                "navn": name, "screenet": False,
                "pep": False, "sanktioner": False, "fund": [],
            })

    return results


# ── Feature 2: Rekursiv koncernstruktur ──────────────────────────────────────

_KONCERN_MAX_DEPTH = 3


async def _build_koncern_node(page, cvr: str, visited: set, depth: int) -> Optional[dict]:
    """
    Henter CVR-data for ét selskab og bygger rekursivt ejerskabstræet.
    Returnerer None ved fejl eller cirkulære referencer.
    """
    if depth > _KONCERN_MAX_DEPTH or cvr in visited:
        return None
    visited.add(cvr)

    data = await _fetch_json(page, GATEWAY_URL.format(cvr=cvr))
    if not data:
        return None

    stamdata   = data.get("stamdata", {}) or {}
    ejere_raw  = (data.get("ejerforhold", {}) or {}).get("aktiveLegaleEjere", []) or []

    children = []
    for ejer in ejere_raw:
        enhedstype = ejer.get("enhedstype", "")
        navn       = ejer.get("senesteNavn", "")
        ejerandel  = _find_ekstra(ejer, "ejerandel-procent-label") or "—"

        if enhedstype == "VIRKSOMHED":
            enhed_id = str(ejer.get("id", ""))
            if enhed_id:
                owner_cvr = await _resolve_owner_cvr(page, enhed_id)
                if owner_cvr and owner_cvr not in visited:
                    child = await _build_koncern_node(page, owner_cvr, visited, depth + 1)
                    if child:
                        child["ejerandel"] = ejerandel
                        children.append(child)
                        continue
            # Fallback: tilføj som leaf node uden børn
            children.append({
                "cvr": None, "navn": navn, "type": "Selskab",
                "status": "—", "ejerandel": ejerandel, "ejere": [],
            })
        else:
            # Person eller ukendt enhed
            children.append({
                "cvr": None, "navn": navn, "type": "Person",
                "status": "—", "ejerandel": ejerandel, "ejere": [],
            })

    return {
        "cvr":      cvr,
        "navn":     stamdata.get("navn", ""),
        "type":     stamdata.get("virksomhedsform", ""),
        "status":   stamdata.get("status", ""),
        "ejerandel": None,   # udfyldes af forælderen
        "ejere":    children,
    }


# ── Feature 3: Historiske adresse- og direktørskift ──────────────────────────

def _parse_historik(response: dict) -> dict:
    """
    Parser virksomhedRegistreringer fra CVR for historiske ændringer.
    Returnerer lister og røde flag baseret på hyppighed.
    """
    registreringer = response.get("virksomhedRegistreringer") or []

    ADRESSE_KEYS  = {"adresse", "adresseaendring", "forretningsadresse", "postadresse"}
    NAVN_KEYS     = {"navn", "navneaendring", "binavne", "bifirmanavn"}
    DIREKTOR_KEYS = {"direktion", "direktoer", "bestyrelsesmedlem",
                     "personkreds", "ledelsesaendring", "tegningsregel"}
    STATUS_KEYS   = {"status", "ophoer", "konkurs", "likvidation", "tvangsoplosning",
                     "betalingsstandsning", "rekonstruktion"}

    adresse_skift  = []
    navn_skift     = []
    direktor_skift = []
    status_skift   = []

    for reg in registreringer:
        titler_raw = reg.get("titelTekstnogler") or []
        titler     = [t.lower().replace("erstdist-organisation-", "").replace("-", "") for t in titler_raw]
        dato       = (reg.get("offentliggoerelseTidsstempel") or "")[:10]
        tekst_node = reg.get("registreringsTekst") or {}
        tekst      = tekst_node.get("tekstUdenLink", "") or ""
        tekst_ren  = re.sub(r"<[^>]+>", " ", tekst).strip()
        tekst_ren  = " ".join(tekst_ren.split())[:200]
        entry = {"dato": dato, "tekst": tekst_ren}

        if any(k in t for t in titler for k in ADRESSE_KEYS):
            adresse_skift.append(entry)
        if any(k in t for t in titler for k in NAVN_KEYS):
            navn_skift.append(entry)
        if any(k in t for t in titler for k in DIREKTOR_KEYS):
            direktor_skift.append(entry)
        if any(k in t for t in titler for k in STATUS_KEYS):
            status_skift.append(entry)

    # ── Røde flag ────────────────────────────────────────────────────────────
    roede_flag = []
    recent_cutoff_adresse  = "2020-01-01"
    recent_cutoff_direktor = "2022-01-01"

    recent_adresse  = [e for e in adresse_skift  if e["dato"] >= recent_cutoff_adresse]
    recent_direktor = [e for e in direktor_skift if e["dato"] >= recent_cutoff_direktor]

    if len(recent_adresse) >= 3:
        roede_flag.append(
            f"{len(recent_adresse)} adresseændringer siden 2020 — mulig ustabilitet i forretningsgrundlaget"
        )
    if len(recent_direktor) >= 3:
        roede_flag.append(
            f"{len(recent_direktor)} ledelses-/direktørskift siden 2022 — undersøg stabilitet"
        )
    if len(navn_skift) >= 2:
        roede_flag.append(
            f"{len(navn_skift)} navneændringer registreret — undersøg årsag"
        )
    if status_skift:
        roede_flag.append("Statusrelaterede registreringer fundet — se detaljer")

    return {
        "adresse_skift":  adresse_skift[:10],
        "navn_skift":     navn_skift[:5],
        "direktor_skift": direktor_skift[:10],
        "status_skift":   status_skift[:5],
        "roede_flag":     roede_flag,
        "total_skift":    len(adresse_skift) + len(navn_skift) + len(direktor_skift),
    }


# ── Feature 4: Retssager & inkasso (Statstidende) ────────────────────────────

async def _fetch_retssager(page, cvr: str, virksomhedsnavn: str) -> dict:
    """
    Søger Statstidende.dk for officielle meddelelser om:
    konkurs, likvidation, tvangsopløsning, rekonstruktion mv.
    Returnerer fundne meddelelser og et samlet risikoniveau.
    """
    resultater = []

    try:
        # Statstidende — søg på CVR-nummer
        search_url = f"https://www.statstidende.dk/S2/Search?q={cvr}"
        await page.goto(search_url, wait_until="domcontentloaded", timeout=18000)
        await page.wait_for_timeout(2000)

        # Prøv flere selector-mønstre — Statstidende ændrer sin HTML
        card_sels = [
            'article', '.search-result', '[class*="result"]',
            'li[class*="item"]', '.announcement', '[class*="announcement"]',
        ]
        cards = []
        for sel in card_sels:
            cards = await page.query_selector_all(sel)
            if cards:
                break

        for card in cards[:8]:
            try:
                tekst = (await card.inner_text()).strip()
                if not tekst:
                    continue
                # Filtrer til relevante meddelelser
                relevant = any(kw in tekst.lower() for kw in [
                    "konkurs", "likvidat", "tvangs", "rekonstrukt",
                    "betalingsstandsning", "bobehandl", cvr,
                    virksomhedsnavn.lower()[:10],
                ])
                if not relevant:
                    continue

                dato = ""
                dato_el = await card.query_selector("time, [class*='date'], [class*='dato']")
                if dato_el:
                    dato = (await dato_el.inner_text()).strip()[:20]

                overskrift = ""
                overskrift_el = await card.query_selector("h2, h3, h4, [class*='title'], [class*='heading']")
                if overskrift_el:
                    overskrift = (await overskrift_el.inner_text()).strip()[:150]

                resultater.append({
                    "kilde":     "Statstidende.dk",
                    "dato":      dato,
                    "overskrift": overskrift or tekst[:100],
                    "tekst":     tekst[:300],
                })
            except Exception:
                continue
    except Exception:
        pass  # Statstidende utilgængelig — returner tom liste

    risiko = "lav"
    if resultater:
        # Tjek om nogen meddelelser er kritiske
        kritiske_kw = ["konkurs", "tvangs", "betalingsstandsning", "rekonstrukt"]
        if any(kw in r["tekst"].lower() for r in resultater for kw in kritiske_kw):
            risiko = "høj"
        else:
            risiko = "middel"

    return {
        "resultater": resultater,
        "risiko":     risiko,
        "noter":      ["Kilde: Statstidende.dk — officielle konkurs- og likvidationsmeddelelser"],
    }


async def fetch_cvr_data(cvr: str) -> dict:
    cvr_clean = cvr.replace(" ", "").strip()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="da-DK",
            )
            page = await context.new_page()

            # Visit homepage to get Cloudflare clearance cookies
            await page.goto("https://datacvr.virk.dk/", wait_until="domcontentloaded", timeout=30000)

            # ── Main company data ──────────────────────────────────────────────
            response = await _fetch_json(page, GATEWAY_URL.format(cvr=cvr_clean))
            if not response:
                raise ValueError(f"Ingen data fundet for CVR {cvr_clean}")

            # ── Financial key figures ──────────────────────────────────────────
            noegletal_data = await _fetch_json(page, NOEGLETAL_URL.format(cvr=cvr_clean))

            # ── Owner due diligence (selskaber) ──────────────────────────────
            owner_risiko = []
            ejere_raw = response.get("ejerforhold", {}).get("aktiveLegaleEjere", []) or []
            for ejer in ejere_raw:
                if ejer.get("enhedstype") == "VIRKSOMHED":
                    enhed_id = ejer.get("id", "")
                    if enhed_id:
                        try:
                            owner_cvr = await _resolve_owner_cvr(page, enhed_id)
                            if owner_cvr:
                                risiko = await _fetch_owner_risiko(page, owner_cvr)
                                risiko["ejerandel"] = _find_ekstra(ejer, "ejerandel-procent-label")
                                owner_risiko.append(risiko)
                        except Exception:
                            # Enkelt ejer-opslag må ikke crashe hele rapporten
                            continue

            # ── Person due diligence (direktører / personkreds) ──────────────
            # Check each person's full company history for bankruptcies
            person_risiko = []
            personkreds = response.get("personkreds", {}) or {}
            seen_person_ids = set()
            for gruppe in personkreds.get("personkredser", []) or []:
                for p in gruppe.get("personRoller", []) or []:
                    enhed_id = str(p.get("id", ""))
                    navn = p.get("senesteNavn", "")
                    # Only check actual persons (not companies) and avoid duplicates
                    if enhed_id and enhed_id not in seen_person_ids and p.get("enhedstype") != "VIRKSOMHED":
                        seen_person_ids.add(enhed_id)
                        try:
                            pr = await _fetch_person_risiko(page, enhed_id, navn)
                            person_risiko.append(pr)
                        except Exception as e:
                            log.warning("person_risiko fejlede for %s (%s): %s", navn, enhed_id, e)
                            person_risiko.append({"navn": navn, "enhed_id": enhed_id, "konkurser": [], "aktive_selskaber": [], "fetch_fejl": True})

            # ── Erhvervsstyrelsens XBRL-register (erstatter proff.dk) ────────
            try:
                proff_data = await fetch_xbrl_data(cvr_clean)
            except Exception as e:
                log.error("XBRL-hentning fejlede for CVR %s: %s", cvr_clean, e)
                proff_data = {"fetch_fejl": True}

            # ── Trustpilot ───────────────────────────────────────────────────
            stam = response.get("stamdata", {}) or {}
            company_name = stam.get("navn", "")
            website      = (stam.get("kontaktoplysninger", {}) or {}).get("hjemmeside", "")
            city         = stam.get("postnummerOgBy", "")
            try:
                trustpilot_data = await _fetch_trustpilot(page, company_name, website, city)
            except Exception as e:
                log.warning("Trustpilot-hentning fejlede for %s: %s", company_name, e)
                trustpilot_data = {}

            # ── Feature 1: PEP & sanctions screening (OpenSanctions) ─────────
            # Saml unikke navne på alle persons (ledelse + personejere)
            alle_navne = []
            personkreds2 = response.get("personkreds", {}) or {}
            for gruppe in personkreds2.get("personkredser", []) or []:
                for p in gruppe.get("personRoller", []) or []:
                    if p.get("enhedstype") != "VIRKSOMHED":
                        n = p.get("senesteNavn", "")
                        if n and n not in alle_navne:
                            alle_navne.append(n)
            ejere_raw2 = (response.get("ejerforhold", {}) or {}).get("aktiveLegaleEjere", []) or []
            for e in ejere_raw2:
                if e.get("enhedstype") != "VIRKSOMHED":
                    n = e.get("senesteNavn", "")
                    if n and n not in alle_navne:
                        alle_navne.append(n)
            try:
                sanctions_data = await _fetch_sanctions(page, alle_navne[:10])
            except Exception as e:
                log.warning("Sanctions-screening fejlede: %s", e)
                sanctions_data = []

            # ── Feature 2: Rekursiv koncernstruktur ───────────────────────────
            try:
                visited_cvr = set()
                koncern_data = await _build_koncern_node(page, cvr_clean, visited_cvr, depth=0)
            except Exception as e:
                log.warning("Koncernstruktur-hentning fejlede for CVR %s: %s", cvr_clean, e)
                koncern_data = {"cvr": cvr_clean, "navn": company_name, "ejere": []}

            # ── Feature 3: Historiske ændringer (synkron parsing) ────────────
            try:
                historik_data = _parse_historik(response)
            except Exception as e:
                log.warning("Historik-parsing fejlede for CVR %s: %s", cvr_clean, e)
                historik_data = {"adresse_skift": [], "navn_skift": [], "direktor_skift": [], "status_skift": [], "roede_flag": []}

            # ── Feature 4: Retssager & inkasso (Statstidende) ────────────────
            try:
                retssager_data = await _fetch_retssager(page, cvr_clean, company_name)
            except Exception:
                retssager_data = {"resultater": [], "risiko": "lav", "noter": []}

        finally:
            # Browser lukkes ALTID — også hvis der sker en fejl undervejs
            try:
                await browser.close()
            except Exception:
                pass

    # ── Feature 5: Tinglysning — køres EFTER browser er lukket så det ikke
    # forsinker rapporten. Har sit eget Playwright-browser-instance.
    try:
        tinglysning_data = await fetch_tinglysning_data(cvr_clean, alle_navne[:2])
    except Exception:
        tinglysning_data = {"tingbog": [], "bilbog": [], "personbog": [], "fejl": None}

    return _normalize(
        response, cvr_clean, noegletal_data,
        owner_risiko, person_risiko, proff_data, trustpilot_data,
        sanctions_data=sanctions_data,
        koncern_data=koncern_data,
        historik_data=historik_data,
        retssager_data=retssager_data,
        tinglysning_data=tinglysning_data,
    )


# ── Keys returned by hentRegnskabsNoegletal ─────────────────────────────────
_NOEGLETAL_KEYS = {
    "nettoomsaetning":   "omsaetning",
    "bruttoresultat":    "bruttoresultat",
    "resultatFoerSkat":  "resultat",
    "aarsresultat":      "resultat",       # fallback
    "egenkapital":       "egenkapital",
    "aktiverIAlt":       "aktiver",
}


def _parse_noegletal_response(noegletal_data) -> dict:
    """
    hentRegnskabsNoegletal — håndterer flere mulige strukturer fra virk.dk API.
    Returnerer {årstal: {omsaetning, bruttoresultat, resultat, egenkapital, aktiver, ansatte}}
    """
    if not noegletal_data:
        return {}

    result = {}

    # Håndter list, {"regnskaber": [...]}, {"list": [...]}, eller {"data": [...]}
    if isinstance(noegletal_data, list):
        entries = noegletal_data
    elif isinstance(noegletal_data, dict):
        entries = (
            noegletal_data.get("regnskaber")
            or noegletal_data.get("list")
            or noegletal_data.get("data")
            or []
        )
    else:
        return {}

    for entry in entries[:5]:
        if not isinstance(entry, dict):
            continue

        # Find årstal fra periodeFormateret ("01-01-2023 - 31-12-2023" → "2023")
        periode = entry.get("periodeFormateret", "") or entry.get("periode", "") or ""
        aar = ""
        if periode:
            # Prøv at finde 4-cifret årstal til sidst i strengen
            hits = re.findall(r'\d{4}', periode)
            aar = hits[-1] if hits else ""
        if not aar:
            log.warning("Nøgletal: kunne ikke udtrække årstal fra periode='%s' — entry springes over", periode)
            continue

        kpis = {}

        # Primær struktur: "noegletal": [{"noegletal": "nettoomsaetning", "vaerdi": 123}]
        for item in (entry.get("noegletal") or []):
            if not isinstance(item, dict):
                continue
            key = item.get("noegletal") or item.get("key") or item.get("name") or ""
            val = item.get("vaerdi") if item.get("vaerdi") is not None else item.get("value")
            if key and val is not None:
                field = _NOEGLETAL_KEYS.get(key)
                if field and field not in kpis:
                    try:
                        kpis[field] = float(val)
                    except (TypeError, ValueError):
                        pass

        # Sekundær struktur: direkte felter på entry-niveau
        for api_key, field in _NOEGLETAL_KEYS.items():
            if field not in kpis and entry.get(api_key) is not None:
                try:
                    kpis[field] = float(entry[api_key])
                except (TypeError, ValueError):
                    pass

        kpis["ansatte"] = entry.get("antalAnsatte") or entry.get("ansatte")
        kpis["periode"] = periode
        result[aar] = kpis

    return result   # {year: {omsaetning, bruttoresultat, ...}}


def _normalize(
    data: dict,
    cvr: str,
    noegletal_data=None,
    owner_risiko=None,
    person_risiko=None,
    proff_data=None,
    trustpilot_data=None,
    sanctions_data=None,
    koncern_data=None,
    historik_data=None,
    retssager_data=None,
    tinglysning_data=None,
) -> dict:
    stamdata    = data.get("stamdata", {})
    ejerforhold = data.get("ejerforhold", {})
    personkreds = data.get("personkreds", {})
    regnskaber  = data.get("sammenhaengendeRegnskaber", []) or []

    # Pre-parse the dedicated noegletal response (may be None)
    noegletal_by_year = _parse_noegletal_response(noegletal_data)

    # ── Ejere ─────────────────────────────────────────────────────────────
    ejere = []
    for e in ejerforhold.get("aktiveLegaleEjere", []):
        ejerandel   = _find_ekstra(e, "ejerandel-procent-label")
        stemmeandel = _find_ekstra(e, "ejerandel-stemmeretprocent-label")
        aendring    = _find_ekstra(e, "ejerandel-aendringsdato-label")
        ejere.append({
            "navn":             e.get("senesteNavn", ""),
            "type":             e.get("enhedstype", ""),
            "ejerandel":        ejerandel or "—",
            "stemmeandel":      stemmeandel or "—",
            "dato_registreret": aendring or "—",
            "besiddelse":       "Indirekte" if e.get("enhedstype") == "VIRKSOMHED" else "Direkte",
        })

    # ── Ledelse ───────────────────────────────────────────────────────────
    rolle_map = {
        "erstdist-organisation-rolle-direktoerer":          "Direktør",
        "erstdist-organisation-rolle-direktion":            "Direktør",
        "erstdist-organisation-rolle-bestyrelsesmedlemmer": "Bestyrelsesmedlem",
        "erstdist-organisation-rolle-bestyrelsesformand":   "Bestyrelsesformand",
        "erstdist-organisation-rolle-stiftere":             "Stifter",
        "erstdist-organisation-rolle-revisorer":            "Revisor",
        "erstdist-organisation-rolle-revision":             "Revisor",
    }
    ledelse_set = {}  # navn → {navn, roller, adresse}
    for gruppe in personkreds.get("personkredser", []):
        rolle_key  = (gruppe.get("rolleTekstnogle") or
                      gruppe.get("rolle", {}).get("tekstnogle") or "")
        rolle_dansk = rolle_map.get(rolle_key, rolle_key)
        for p in gruppe.get("personRoller", []):
            navn = p.get("senesteNavn", "")
            if navn:
                if navn not in ledelse_set:
                    ledelse_set[navn] = {
                        "navn":    navn,
                        "roller":  [],
                        "adresse": p.get("adresse", ""),
                    }
                if rolle_dansk not in ledelse_set[navn]["roller"]:
                    ledelse_set[navn]["roller"].append(rolle_dansk)

    ledelse = [
        {"navn": v["navn"], "roller": ", ".join(v["roller"]), "adresse": v["adresse"]}
        for v in ledelse_set.values()
    ]

    # ── Regnskaber ────────────────────────────────────────────────────────
    # Priority: noegletal_by_year (dedicated endpoint) > inline noegletal in hentVirksomhed
    parsed_regnskaber = []
    for r in regnskaber[:4]:
        reg_list = r.get("regnskaber", [])
        if not reg_list:
            continue
        reg = reg_list[0]

        periode = r.get("periodeFormateret", "")
        aar     = periode[-4:] if len(periode) >= 4 else ""

        # Try dedicated noegletal first
        if aar and aar in noegletal_by_year:
            kpis = noegletal_by_year[aar]
        else:
            # Fall back to inline noegletal inside hentVirksomhed response
            inline = {
                n.get("noegletal"): n.get("vaerdi")
                for n in (reg.get("noegletal") or [])
            }
            kpis = {
                "omsaetning":     inline.get("nettoomsaetning"),
                "bruttoresultat": inline.get("bruttoresultat"),
                "resultat":       inline.get("resultatFoerSkat") or inline.get("aarsresultat"),
                "egenkapital":    inline.get("egenkapital"),
                "aktiver":        inline.get("aktiverIAlt"),
                "ansatte":        reg.get("antalAnsatte"),
            }

        parsed_regnskaber.append({
            "periode":        periode,
            "aar":            aar,
            "omsaetning":     kpis.get("omsaetning"),
            "bruttoresultat": kpis.get("bruttoresultat"),
            "resultat":       kpis.get("resultat"),
            "egenkapital":    kpis.get("egenkapital"),
            "aktiver":        kpis.get("aktiver"),
            "ansatte":        kpis.get("ansatte"),
        })

    # ── Tegningsregel ─────────────────────────────────────────────────────
    tegning = personkreds.get("tegningsregel", "")

    # ── Stamdata extras ───────────────────────────────────────────────────
    kontaktoplysninger = stamdata.get("kontaktoplysninger", {}) or {}
    branche = stamdata.get("branche", {}) or {}

    # ── Risiko / historik ─────────────────────────────────────────────────
    # Konkurs/status-historik: se om selskabet nogensinde har været under konkurs
    hist_statusser = (data.get("historiskStamdata", {}) or {}).get("status", []) or []
    konkurs_statuser = [
        s for s in hist_statusser
        if any(k in str(s).upper() for k in ["KONKURS", "TVANGS", "LIKVIDATION", "OPHOERT"])
    ]

    # Virksomhedsregistreringer: officielle meddelelser (adresseændringer, kapital, navne, konkurs)
    registreringer_raw = data.get("virksomhedRegistreringer") or []
    registreringer = []
    for reg in registreringer_raw[:20]:  # max 20 seneste
        titler = reg.get("titelTekstnogler") or []
        tekst_node = reg.get("registreringsTekst") or {}
        tekst = tekst_node.get("tekstUdenLink", "") or ""
        # Rens XML-tags
        import re as _re
        tekst_ren = _re.sub(r"<[^>]+>", " ", tekst).strip()
        tekst_ren = " ".join(tekst_ren.split())[:300]
        registreringer.append({
            "dato":   reg.get("offentliggoerelseTidsstempel", ""),
            "titler": titler,
            "tekst":  tekst_ren,
        })

    # Meddelelser (betalingsstandsning, tvangsopløsning mv.)
    meddelelser = data.get("virksomhedsMeddelelser") or []

    # AML/hvidvask-markering fra stamdata
    hvidvask = stamdata.get("omfattetAfHvidvaskloven", False)

    # ── Proff.dk fallback: brug virk.dk nøgletal hvis proff.dk fejlede ────────
    # Virk.dk API returnerer tal i hele DKK — konverter til t.DKK (÷1000)
    def _to_tdkk(val):
        if val is None:
            return None
        try:
            v = float(val)
            # Virk.dk returnerer i DKK (f.eks. 58.789.331) → t.DKK = ÷1000
            # Proff.dk returnerer allerede i t.DKK (f.eks. 58.789)
            # Skelner: hvis absolut værdi > 500.000 antages det er DKK → konverter
            return round(v / 1000, 1) if abs(v) > 500_000 else round(v, 1)
        except (TypeError, ValueError):
            return None

    if not proff_data or not proff_data.get("regnskaber"):
        if parsed_regnskaber:
            fallback_regnskaber = []
            for r in parsed_regnskaber:
                aar_str = (r["aar"] + "-12") if r["aar"] else ""
                fallback_regnskaber.append({
                    "aar":               aar_str,
                    "bruttofortjeneste": _to_tdkk(r.get("bruttoresultat")),
                    "resultat":          _to_tdkk(r.get("resultat")),
                    "egenkapital":       _to_tdkk(r.get("egenkapital")),
                    "balance":           _to_tdkk(r.get("aktiver")),
                    "ebit":              None,
                })
            proff_data = {
                "regnskaber":  fallback_regnskaber,
                "ansatte":     parsed_regnskaber[0].get("ansatte") if parsed_regnskaber else None,
                "seneste_aar": (parsed_regnskaber[0]["aar"] + "-12") if parsed_regnskaber and parsed_regnskaber[0].get("aar") else "",
                "kilde":       "virk.dk",
            }
    else:
        # XBRL fra Erhvervsstyrelsen er allerede i t.DKK — spring konvertering over.
        # Gælder kun legacy-kilder (proff.dk, manuel upload mv.)
        if proff_data.get("kilde") != "erhvervsstyrelsen":
            for reg in proff_data.get("regnskaber", []):
                for felt in ("bruttofortjeneste", "resultat", "egenkapital", "balance", "ebit"):
                    reg[felt] = _to_tdkk(reg.get(felt))

    return {
        "cvr":               stamdata.get("cvrnummer", cvr),
        "name":              stamdata.get("navn", ""),
        "address":           stamdata.get("adresse", ""),
        "city":              stamdata.get("postnummerOgBy", ""),
        "full_address":      f"{stamdata.get('adresse', '')}, {stamdata.get('postnummerOgBy', '')}",
        "company_type":      stamdata.get("virksomhedsform", ""),
        "start_date":        stamdata.get("startdato", ""),
        "end_date":          stamdata.get("ophoersdato"),
        "status":            stamdata.get("status", ""),
        "email":             kontaktoplysninger.get("email", ""),
        "phone":             kontaktoplysninger.get("telefon", ""),
        "industry_code":     branche.get("branchekode", ""),
        "industry_desc":     branche.get("branchetekst", ""),
        "protected":         stamdata.get("reklamebeskyttet", False),
        "ejere":             ejere,
        "ledelse":           ledelse,
        "regnskaber":        parsed_regnskaber,
        "tegningsregel":     tegning,
        "ansatte":           data.get("antalAnsatte", {}).get("antalAnsatte"),
        # ── Risiko-data fra CVR ──
        "konkurs_historik":  konkurs_statuser,        # tomt = aldrig konkurs
        "registreringer":    registreringer,           # officielle CVR-meddelelser
        "meddelelser":       meddelelser,              # særlige meddelelser
        "hvidvask_omfattet": hvidvask,                 # om virk. er under hvidvaskloven
        "ejer_risiko":       owner_risiko or [],       # konkurshistorik per ejer-selskab
        "person_risiko":     person_risiko or [],      # konkurshistorik per person (direktør mv.)
        # ── Proff.dk supplement (t.DKK = tusinde DKK) ──
        "proff_data":        proff_data or {},         # omsaetning, resultat, ansatte fra proff.dk
        # ── Trustpilot ──
        "trustpilot":        trustpilot_data or {},    # rating, antal, seneste anmeldelser
        # ── De 4 nye features ──
        "pep_screening":     sanctions_data  or [],    # [{navn, screenet, pep, sanktioner, fund}]
        "koncern_struktur":  koncern_data    or {},    # rekursivt ejerskabstræ
        "historik":          historik_data   or {},    # adresse/navn/direktor-skift + røde flag
        "retssager":         retssager_data  or {},    # statstidende-fund + risiko
        # ── Feature 5: Tinglysning ──
        "tinglysning":       tinglysning_data or {},   # tingbog, bilbog, personbog
    }


def _find_ekstra(owner: dict, key: str) -> str:
    for item in owner.get("ekstraDataList", []):
        if item.get("tekstnogle") == key:
            return item.get("vaerdi", "")
    return ""
