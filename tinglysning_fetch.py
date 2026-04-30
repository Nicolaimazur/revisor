"""
Tinglysning data via tinglysning.dk's offentlige søgegrænseflade.

Henter data fra tre registre uden login:
  - Tingbogen   → fast ejendom ejet af virksomheden, panter, hæftelser
  - Bilbogen    → køretøjer registreret på CVR-nummeret
  - Personbogen → virksomhedspant + hæftelser på direktører/ejere (navn-søgning)

Anvender Playwright (headless Chromium) — samme teknik som datacvr.virk.dk.
"""

import asyncio
import logging
import re
import traceback
from typing import Optional

from playwright.async_api import async_playwright, Page

log = logging.getLogger("tinglysning_fetch")
if not log.handlers:
    logging.basicConfig(level=logging.INFO)

BASE_URL = "https://www.tinglysning.dk"

# Timeout i ms for side-navigation
NAV_TIMEOUT  = 30_000
# Timeout for at vente på dynamisk indhold
WAIT_TIMEOUT = 15_000


# ── Offentlig entry point ──────────────────────────────────────────────────────

async def fetch_tinglysning_data(cvr: str, person_navne: list[str] | None = None) -> dict:
    """
    Hent tinglysningsdata for en virksomhed.

    Args:
        cvr:           CVR-nummer (8 cifre)
        person_navne:  Liste af navne på direktører/ejere til personbog-søgning

    Returns:
        {
          "tingbog":    [...],   # Ejendomme med hæftelser
          "bilbog":     [...],   # Køretøjer
          "personbog":  [...],   # Virksomhedspant + personlige hæftelser
          "fejl":       str|None # Eventuel fejlbesked
        }
    """
    cvr_clean = str(cvr).strip().replace(" ", "")
    if len(cvr_clean) != 8 or not cvr_clean.isdigit():
        return _tom_resultat("Ugyldigt CVR-nummer")

    person_navne = person_navne or []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-http2"],  # tinglysning.dk understøtter ikke HTTP/2
            )
            try:
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="da-DK",
                    # Tinglysning.dk understøtter ikke HTTP/2
                    extra_http_headers={"Connection": "keep-alive"},
                )
                page = await context.new_page()

                # Besøg forsiden for at hente cookies/session
                log.info("Tinglysning: indlæser forside...")
                await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                await page.wait_for_timeout(3000)

                # Log hvad siden faktisk viser efter load
                side_url   = page.url
                side_title = await page.title()
                side_tekst = await page.evaluate("() => document.body?.innerText?.slice(0,300) || ''")
                log.info("Tinglysning forside url=%s title=%s tekst=%s", side_url, side_title, side_tekst[:150])

                # Tingbog
                tingbog = await _soeg_tingbog(page, cvr_clean)

                # Bilbog (separat side)
                bilbog = await _soeg_bilbog(page, cvr_clean)

                # Personbog: søg på hvert personnavn separat
                personbog = []
                for navn in person_navne[:2]:   # maks 2 personer for at spare tid
                    pb = await _soeg_personbog(page, cvr_clean, navn)
                    personbog.extend(pb)

                log.info(
                    "Tinglysning cvr=%s: tingbog=%d bilbog=%d personbog=%d",
                    cvr_clean, len(tingbog), len(bilbog), len(personbog),
                )
                return {
                    "tingbog":   tingbog,
                    "bilbog":    bilbog,
                    "personbog": personbog,
                    "fejl":      None,
                }
            finally:
                await browser.close()

    except Exception as e:
        log.error("Tinglysning cvr=%s fejlede: %s\n%s", cvr, e, traceback.format_exc())
        return _tom_resultat(str(e))


# ── Tingbog ───────────────────────────────────────────────────────────────────

async def _soeg_tingbog(page: Page, cvr: str) -> list:
    """Søg i Tingbogen på CVR-nummer — returnerer ejendomme med hæftelser."""
    try:
        url = f"{BASE_URL}/#/forespørgsel/tingbogen/virksomhed/{cvr}"
        log.info("Tinglysning tingbog url=%s", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await page.wait_for_timeout(4000)   # Giv SPA tid til at rendere

        # Debug: log hvad siden faktisk viser
        aktuel_url = page.url
        tekst = await page.evaluate("() => document.body?.innerText?.slice(0,400) || ''")
        log.info("Tingbog efter nav: url=%s tekst=%s", aktuel_url, tekst[:200].replace('\n', ' '))

        # Tjek om siden kræver login
        if await _kræver_login(page):
            log.warning("Tinglysning tingbog: kræver login")
            return []

        # Vent på dynamisk indhold
        await _vent_paa_indhold(page, [
            "[data-test='result-row']",
            ".result-list",
            "table tbody tr",
            ".no-results",
            "[class*='result']",
            "h1", "h2",
        ])

        return await _udtræk_tingbog_resultater(page)

    except Exception as e:
        log.warning("Tingbog søgning fejlede: %s", e)
        return []


async def _soeg_tingbog_alternativ(page: Page, cvr: str) -> list:
    """Alternativ: brug søgeformular på forsiden."""
    try:
        await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        await page.wait_for_timeout(1500)

        # Find CVR-søgefelt
        cvr_felt = await _find_input(page, [
            "input[name='cvr']",
            "input[placeholder*='CVR']",
            "input[placeholder*='cvr']",
            "#cvr-search",
            "input[type='text']",
        ])

        if not cvr_felt:
            log.warning("Tingbog: ingen søgeformular fundet")
            return []

        await cvr_felt.fill(cvr)
        await page.keyboard.press("Enter")
        await _vent_paa_indhold(page, ["[data-test='result-row']", ".result-list", "table tbody tr"])

        return await _udtræk_tingbog_resultater(page)

    except Exception as e:
        log.warning("Tingbog alternativ søgning fejlede: %s", e)
        return []


async def _udtræk_tingbog_resultater(page: Page) -> list:
    """Udtræk ejendomsresultater fra den aktuelle side."""
    try:
        # Prøv at hente data via JavaScript fra DOM
        resultater = await page.evaluate("""
            () => {
                const rows = Array.from(
                    document.querySelectorAll('table tbody tr, [data-test="result-row"], .result-item')
                );
                return rows.map(row => ({
                    tekst: row.innerText.trim().replace(/\\s+/g, ' ')
                })).filter(r => r.tekst.length > 5);
            }
        """)

        ejendomme = []
        for r in (resultater or []):
            tekst = r.get("tekst", "").strip()
            if tekst and "ingen" not in tekst.lower():
                ejendomme.append({"beskrivelse": tekst})

        log.info("Tingbog: fandt %d rækker", len(ejendomme))
        return ejendomme

    except Exception as e:
        log.warning("Udtræk tingbog fejlede: %s", e)
        return []


# ── Bilbog ────────────────────────────────────────────────────────────────────

async def _soeg_bilbog(page: Page, cvr: str) -> list:
    """Søg i Bilbogen på CVR-nummer."""
    try:
        url = f"{BASE_URL}/#/forespørgsel/bilbogen/virksomhed/{cvr}"
        log.info("Tinglysning bilbog url=%s", url)

        # Åbn ny side for at undgå state-konflikter med tingbog
        context = page.context
        bil_page = await context.new_page()
        try:
            await bil_page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await _vent_paa_indhold(bil_page, [
                "table tbody tr",
                "[data-test='result-row']",
                ".no-results",
                "[class*='result']",
            ])

            if await _kræver_login(bil_page):
                log.warning("Bilbog: kræver login")
                return []

            resultater = await bil_page.evaluate("""
                () => {
                    const rows = Array.from(
                        document.querySelectorAll('table tbody tr, [data-test="result-row"]')
                    );
                    return rows.map(row => ({
                        tekst: row.innerText.trim().replace(/\\s+/g, ' ')
                    })).filter(r => r.tekst.length > 5);
                }
            """)

            koeretoejer = []
            for r in (resultater or []):
                tekst = r.get("tekst", "").strip()
                if tekst and "ingen" not in tekst.lower():
                    koeretoejer.append({"beskrivelse": tekst})

            log.info("Bilbog: fandt %d køretøjer", len(koeretoejer))
            return koeretoejer

        finally:
            await bil_page.close()

    except Exception as e:
        log.warning("Bilbog søgning fejlede: %s", e)
        return []


# ── Personbog ─────────────────────────────────────────────────────────────────

async def _soeg_personbog(page: Page, cvr: str, navn: str) -> list:
    """
    Søg i Personbogen.
    Virksomhedspant søges på CVR. Personlige hæftelser søges på navn.
    """
    resultater = []
    try:
        # Virksomhedspant (CVR-baseret)
        url_cvr = f"{BASE_URL}/#/forespørgsel/personbogen/virksomhed/{cvr}"
        context  = page.context
        pb_page  = await context.new_page()
        try:
            await pb_page.goto(url_cvr, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            await _vent_paa_indhold(pb_page, [
                "table tbody tr", "[data-test='result-row']", ".no-results"
            ])

            if not await _kræver_login(pb_page):
                rækker = await pb_page.evaluate("""
                    () => Array.from(
                        document.querySelectorAll('table tbody tr, [data-test="result-row"]')
                    ).map(r => ({ tekst: r.innerText.trim().replace(/\\s+/g, ' ') }))
                     .filter(r => r.tekst.length > 5)
                """)
                for r in (rækker or []):
                    tekst = r.get("tekst", "").strip()
                    if tekst and "ingen" not in tekst.lower():
                        resultater.append({"type": "virksomhedspant", "navn": navn, "beskrivelse": tekst})
        finally:
            await pb_page.close()

    except Exception as e:
        log.warning("Personbog CVR søgning fejlede: %s", e)

    log.info("Personbog cvr=%s navn=%s: fandt %d poster", cvr, navn, len(resultater))
    return resultater


# ── Hjælpefunktioner ──────────────────────────────────────────────────────────

async def _vent_paa_indhold(page: Page, selektorer: list[str], timeout: int = WAIT_TIMEOUT):
    """Vent til én af de angivne selektorer er synlig, eller timeout."""
    try:
        tasks = [
            page.wait_for_selector(sel, timeout=timeout, state="visible")
            for sel in selektorer
        ]
        done, _ = await asyncio.wait(
            [asyncio.ensure_future(t) for t in tasks],
            return_when=asyncio.FIRST_COMPLETED,
            timeout=timeout / 1000,
        )
        # Annullér resterende tasks
        for task in _:
            task.cancel()
    except Exception:
        # Timeout er OK — siden er måske loadet uden de forventede selektorer
        await page.wait_for_timeout(2000)


async def _kræver_login(page: Page) -> bool:
    """Returnerer True hvis siden viderestiller til NemLog-in."""
    url = page.url.lower()
    return "nemlog-in" in url or "login" in url or "saml" in url


async def _find_input(page: Page, selektorer: list[str]):
    """Returnerer første input-element der matcher en af selektorerne."""
    for sel in selektorer:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                return el
        except Exception:
            continue
    return None


def _tom_resultat(fejl: str | None = None) -> dict:
    return {"tingbog": [], "bilbog": [], "personbog": [], "fejl": fejl}
