"""
Uses Claude claude-sonnet-4-6 to generate all analysis sections of the due diligence report.
Combines CVR data with Claude's knowledge — same approach as generating reports directly in chat.
"""

import json
import os
from datetime import date

import anthropic


def _client() -> anthropic.Anthropic:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY mangler i miljøvariable (.env)")
    return anthropic.Anthropic(api_key=api_key)


SYSTEM_PROMPT = """Du er en erfaren revisor og AML-compliance-specialist i Danmark.
Du udarbejder Kunde Due Diligence (KDD) rapporter til brug ved revisor onboarding af nye kunder.

DATAKILDER OG PRIORITET:
1. CVR-DATA er factuelt grundlag — disse felter MÅ ALDRIG ændres eller overskrives:
   - Navn, adresse, CVR-nummer, stiftelsesdato, status, selskabsform
   - Ejere (navn, ejerandel, stemmeandel, dato) — kopieres præcist
   - Ledelse / direktion (navn, roller) — kopieres præcist
   - Tegningsregel — kopieres præcist

2. SUPPLERENDE VIDEN — brug din viden til felter der IKKE er dækket af CVR-data:
   - Selskabsbeskrivelse og formål
   - Brancheanalyse og benchmarking
   - Finansielle nøgletal hvis CVR-data viser null (brug kun offentliggjorte årsrapporter)
   - Ledelsesprofiler (baggrundsinfo om personerne)
   - Søgeresultater / mediesager

VIGTIGE REGLER:
- Svar KUN med et JSON-objekt — ingen forklaring, ingen markdown-kodeblok, kun rent JSON.
- Ejere og ledelse fra CVR-data kopieres 1:1 — du må IKKE opfinde andre ejere eller ledere.
- Hvis CVR viser null for finansielle tal, brug da offentliggjorte årsrapporter. Er disse også ukendte, skriv null.
- Risikovurderinger baseres på fakta — undgå gætteri.
- Alle tekstfelter på dansk. Datoformat: DD-MM-YYYY.

RISIKO-DATA FRA CVR (brug disse fakta direkte):
- `status`: Aktuel selskabsstatus. "NORMAL" = intet problem. "UNDER KONKURS" / "TVANGSOPLØST" = kritisk risiko.
- `konkurs_historik`: Liste over tidligere konkurs/ophørs-statusser. Tom liste = ingen historik = lav risiko.
- `registreringer`: Officielle CVR-meddelelser (kapitalændringer, navneskift, ledelsesændringer). Læs disse for mønstre.
- `meddelelser`: Særlige meddelelser (betalingsstandsning, tvangsopløsning mv.). Tom = intet problem.
- `hvidvask_omfattet`: Om virksomheden er direkte omfattet af hvidvaskloven.
- `ejer_risiko`: Liste over ejer-selskabers egne CVR-data inkl. status og konkurshistorik. Brug til at vurdere om ejerselskaberne selv har haft problemer.
- `person_risiko`: Liste over direktørers/lederes FULDE selskabshistorik fra CVR. Hvert element indeholder `konkurser` — en liste over selskaber der er "OPLØST EFTER KONKURS" hvor personen var direktør/stifter. Brug dette som et KRITISK risikosignal — en person med mange konkurser er en høj risiko.
- `proff_data`: Flerårigt regnskab scraped fra proff.dk. Struktur: `{regnskaber: [{aar, bruttofortjeneste, resultat, egenkapital, balance, ebit}, ...], ansatte, seneste_aar}`. Alle beløb i t.DKK. `regnskaber[0]` er seneste år. Disse tal er næsten altid til stede for A/S, ApS og P/S.
- `pep_screening`: Automatisk screening af ledelse/ejere mod OpenSanctions. Struktur: `[{navn, screenet, pep, sanktioner, fund: [{match_navn, score, pep, sanktioner, datasets}]}]`. `screenet=false` = API-fejl eller ingen svar. `pep=true` eller `sanktioner=true` = kritisk fund der kræver opfølgning. Brug disse data direkte i sektion_02 PEP-screening.
- `koncern_struktur`: Rekursivt ejerskabstræ op til 3 niveauer. Struktur: `{cvr, navn, type, status, ejerandel, ejere: [{...rekursivt...}]}`. Brug dette til at beskrive den samlede koncernstruktur og identificere ultimative beneficial owners (UBO).
- `historik`: Historiske ændringer fra CVR-registreringer. Felter: `adresse_skift [{dato, tekst}]`, `navn_skift`, `direktor_skift`, `status_skift`, `roede_flag [strenge]`. Hyppige skift er røde flag — brug `roede_flag` direkte i risikovurderingen.
- `retssager`: Fund fra Statstidende.dk. Felter: `resultater [{kilde, dato, overskrift, tekst}]`, `risiko ("lav"|"middel"|"høj")`. Tom `resultater` = ingen fund = lav risiko. Fund indikerer konkurs/likvidationsbehandling eller rekonstruktion.
- `trustpilot`: Trustpilot-data hvis tilgængeligt: `{rating, antal, reviews: [{stjerner, titel, tekst, dato}]}`. Brug til at vurdere kundeoplevelse og evt. røde flag (svindel, manglende levering, betalingsproblemer). Tom = ingen Trustpilot-profil fundet.

Risikoniveauer:
- "lav"    → grøn (ingen bekymringer)
- "middel" → orange (opmærksomhed krævet)
- "høj"    → rød (kritisk)
- "ukendt" → grå (ikke screenet)
"""

USER_PROMPT_TEMPLATE = """
Udarbejd en komplet Kunde Due Diligence rapport for følgende virksomhed.

═══════════════════════════════════════════
CVR-DATA (verificerede fakta fra virk.dk):
═══════════════════════════════════════════
{cvr_json}

DATO I DAG: {today}

INSTRUKTION:
OVERORDNEDE REGLER:
- CVR-data er AUTORITATIV FAKTA — brug præcist hvad der fremgår: cvr, name, address, city, company_type, start_date, status, ejere, ledelse.
- Skriv på professionelt dansk som en erfaren statsautoriseret revisor.
- Vær konkret og faktabaseret. Undgå generiske vendinger som "det bemærkes" eller "det er vigtigt".
- Alle risikovurderinger skal begrundes med specifikke observationer fra data.
- Brug ALDRIG betegnelser som "AI-estimat", "AI-viden", "ifølge AI" eller lignende. Angiv i stedet "Baseret på offentligt tilgængeligt materiale" eller udelad kildehenvisningen helt hvis den er overflødig.

FINANSIELLE TAL:
- Brug `proff_data.regnskaber` (op til 5 år, seneste år først). Hvert element: `aar` (YYYY-MM), `bruttofortjeneste`, `resultat`, `egenkapital`, `balance`, `ebit` (alle i t.DKK).
- `aar_kolonner`: Årstallet (første 4 cifre af `aar`) for hvert regnskabsår, seneste først. Eks: ["2024","2023","2022"].
- `regnskabspost_tabel`: Én række per post. `vaerdier` = ét tal per år i `aar_kolonner`. Format: "X.XXX t.DKK". Null hvis mangler.
- Udelad poster hvor alle værdier er null.
- `sektion_01.seneste_regnskabsaar`: `aar_kolonner[0]` (YYYY). Null hvis ingen data.
- `sektion_01.naeste_regnskabsfrist`: Seneste regnskabsår + 1 år + 5 måneder → "31/05-YYYY". Null hvis ingen data.
- NYREGISTRERET SELSKAB: Hvis `proff_data` er tom ({{}}) eller `proff_data.regnskaber` er en tom liste, betyder det at selskabet endnu ikke har indberettet årsregnskab. Sæt da `sektion_04.ingen_regnskaber_note` = "Virksomheden er nyregistreret og har endnu ikke indberettet årsregnskab til Erhvervsstyrelsen. Finansielle nøgletal er derfor ikke tilgængelige." og sæt `sektion_04.aar_kolonner` = [] og `sektion_04.regnskabspost_tabel` = [].

FINANSIEL ANALYSE (sektion_04.finansiel_analyse):
- Analyser udviklingen over alle tilgængelige år. Beregn vækstrater og margins.
- Vurder: soliditetsgrad (egenkapital/balance), likviditet, rentabilitet.
- Sammenlign med branchegennemsnit hvor relevant.
- Identificer konkrete røde flag: negativ egenkapital, faldende omsætning 3+ år, margin-kompression.
- Skriv 3-5 præcise afsnit. Undgå generiske fraser.

AML/PEP SCREENING (sektion_02.aml_screening):
- Giv en reel vurdering baseret på offentlig viden om ledelsespersoner.
- `samlet_risiko`: "Lav" hvis ukendte privatpersoner uden røde flag. "Middel" hvis udenlandsk tilknytning eller kompleks ejerstruktur. "Høj" hvis konkurshistorik, sanktioner eller PEP-eksponering.
- Vær specifik — nævn navne og konkrete observationer.

PÅTEGNINGSHISTORIK (sektion_04.paategninger.historik):
- Brug `proff_data` + din viden. Påtegningstyper: "Revisionspåtegning uden forbehold", "Revisionspåtegning med forbehold", "Review-erklæring", "Assistanceerklæring".
- `revisor`="Ukendt" hvis ikke tilgængeligt.

LEDELSESPORTEFØLJE (sektion_08.ledelsesprofiler[].selskaber):
- De seneste 5 selskaber fra `person_risiko` (aktive + ophørte). `konkurs`=true hvis KONKURS i status.

RISIKOVURDERING (sektion_09):
- `risiko_niveau`: "LAV" / "MIDDEL" / "HØJ" — baseret på en samlet vurdering af alle sektioner.
- `samlet_vurdering`: 2-3 præcise afsnit der begrunder niveauet med konkrete fund.
- Lav = veletableret selskab, positiv økonomi, kendt ledelse, ingen røde flag.
- Middel = nogle usikkerheder men grundlæggende sund.
- Høj = konkrete røde flag (negativ EK, kompleks struktur, ukendte ejere, konkurshistorik).

For øvrige analyse-felter: Brug din viden om virksomheden og branchen.

Returner dette præcise JSON-objekt (ingen kommentarer, kun gyldigt JSON):

{{
  "meta": {{
    "rapport_dato": "{today}",
    "cvr": "<cvr fra CVR-data>",
    "selskabsnavn": "<navn fra CVR-data>",
    "adresse": "<fuld_adresse fra CVR-data>"
  }},

  "risiko_oversigt": [
    {{
      "indikator": "Konkurs / betalingsstandsning",
      "vurdering": "<brug CVR: hvis status=NORMAL og konkurs_historik=[] → 'lav'; ellers 'høj'>",
      "status_tekst": "<f.eks. 'Ingen konkurshistorik registreret i CVR' eller 'Under konkurs siden XX'>",
      "bemærkning": "<faktuel bemærkning baseret på status + konkurs_historik + meddelelser>"
    }},
    {{
      "indikator": "Selskabsstatus (CVR)",
      "vurdering": "<'lav' hvis NORMAL, 'høj' hvis UNDER KONKURS/TVANGSOPLØST>",
      "status_tekst": "<aktuel status fra CVR>",
      "bemærkning": "<evt. noter om historiske statusændringer fra registreringer>"
    }},
    {{
      "indikator": "UBO-registrering",
      "vurdering": "<'lav' hvis alle ejere er registreret med ejerandel; 'middel' hvis noget mangler>",
      "status_tekst": "<kort status>",
      "bemærkning": "<bemærkning om ejerskabsstruktur ift. hvidvasklov>"
    }},
    {{
      "indikator": "Negativ presse / mediesager",
      "vurdering": "<brug din viden om virksomheden>",
      "status_tekst": "<kort status>",
      "bemærkning": "<hvad er fundet / ikke fundet>"
    }},
    {{
      "indikator": "Negativ finansiel trend",
      "vurdering": "<baseret på regnskabsdata eller din viden>",
      "status_tekst": "<kort status>",
      "bemærkning": "<bemærkning>"
    }},
    {{
      "indikator": "PEP / sanktioner",
      "vurdering": "ukendt",
      "status_tekst": "Ikke screenet",
      "bemærkning": "Kræver manuel screening mod anerkendt PEP/sanktionsdatabase (f.eks. World-Check, Experian PEP)"
    }},
    {{
      "indikator": "Kompleks koncernstruktur",
      "vurdering": "<baseret på ejerskabsstruktur fra CVR>",
      "status_tekst": "<kort status>",
      "bemærkning": "<antal holdingselskaber, indirekte ejerskab mv.>"
    }},
    {{
      "indikator": "Hvidvask-risiko (branche)",
      "vurdering": "<baseret på branche og hvidvask_omfattet>",
      "status_tekst": "<kort status>",
      "bemærkning": "<branchens hvidvaskrisiko + om virk. er direkte omfattet af hvidvaskloven>"
    }}
  ],

  "sektion_01": {{
    "selskabsnavn": "<navn fra CVR-data — kopieres præcist>",
    "cvr_nummer": "<cvr fra CVR-data>",
    "selskabsform": "<company_type fra CVR-data>",
    "adresse": "<full_address fra CVR-data>",
    "stiftelsesdato": "<start_date fra CVR-data>",
    "branchekode": "<industry_code — beskrivelse fra CVR-data, eller din viden>",
    "formaal": "<virksomhedens formål>",
    "status": "<status fra CVR-data>",
    "reklamebeskyttet": "<Ja hvis protected=true, ellers Nej>",
    "regnskabspligt": "Ja",
    "website": "<domæne hvis kendt>",
    "seneste_regnskabsaar": "<YYYY fra aar_kolonner[0], eller null>",
    "naeste_regnskabsfrist": "<31/05-YYYY beregnet fra seneste_regnskabsaar+1, eller null>",
    "selskabsbeskrivelse": "<2-4 sætninger om virksomheden>",
    "kontorer": [{{"land_by": "<by>", "funktion": "<funktion>"}}]
  }},

  "sektion_02": {{
    "direktion": [
      {{"navn": "<navn fra CVR ledelse>", "rolle": "<roller fra CVR ledelse>", "tilknyttet_siden": "<dato hvis tilgængeligt>", "bemaerkning": "<note>"}}
    ],
    "reelle_ejere": [
      {{"navn": "<navn fra CVR ejere — kopieres præcist>", "ejerandel": "<ejerandel fra CVR>", "stemmeandel": "<stemmeandel fra CVR>", "besiddelse": "<besiddelse fra CVR>", "dato_registreret": "<dato_registreret fra CVR>"}}
    ],
    "ubo_note": "<note om UBO og hvidvasklov>",
    "ledelsesprofiler": [
      {{"navn": "<navn fra CVR ledelse>", "titel": "<titel>", "beskrivelse": "<2-3 sætninger baseret på offentlig viden>"}}
    ],
    "pep_screening": [
      {{
        "navn": "<navn fra pep_screening-data>",
        "screenet": "<true|false — fra pep_screening.screenet>",
        "pep": "<true|false — fra pep_screening.pep>",
        "sanktioner": "<true|false — fra pep_screening.sanktioner>",
        "samlet_risiko": "<'lav' hvis screenet=true og pep=false og sanktioner=false; 'høj' hvis pep=true eller sanktioner=true; 'ukendt' hvis screenet=false>",
        "fund_detaljer": "<beskriv evt. fund fra pep_screening.fund[].match_navn + datasets, eller 'Ingen fund i OpenSanctions-database'>",
        "anbefaling": "<'Ingen yderligere screening krævet' eller 'Kræver manuel verifikation mod World-Check / Experian PEP'>"
      }}
    ],
    "aml_screening": {{
      "noter": "<samlet vurdering af PEP/sanktions-risiko baseret på pep_screening-data>"
    }},
    "person_due_diligence": [
      {{
        "navn": "<navn fra person_risiko>",
        "aktive_selskaber_antal": "<antal aktive selskaber>",
        "konkurs_antal": "<antal selskaber med OPLØST EFTER KONKURS — fra person_risiko.konkurser>",
        "konkurser": [
          {{"cvr": "<cvr>", "navn": "<selskabsnavn>", "rolle": "<rolle>", "status": "<status>"}}
        ],
        "risiko_vurdering": "<'lav' hvis 0 konkurser; 'middel' hvis 1-2; 'høj' hvis 3+>",
        "bemaerkning": "<faktuel bemærkning baseret på CVR-data>"
      }}
    ],
    "ejer_selskaber_due_diligence": [
      {{
        "navn": "<ejerens navn fra ejer_risiko>",
        "cvr": "<cvr fra ejer_risiko>",
        "status": "<status fra ejer_risiko — NORMAL / UNDER KONKURS etc.>",
        "selskabsform": "<selskabsform>",
        "stiftelsesdato": "<dato>",
        "ejerandel": "<ejerandel>",
        "konkurs_historik": "<'Ingen konkurshistorik' eller beskrivelse af fund>",
        "risiko_vurdering": "lav|middel|høj",
        "bemaerkning": "<kort vurdering baseret på CVR-data>"
      }}
    ]
  }},

  "sektion_03": {{
    "beskrivelse": "<tekst om ejerstruktur baseret på CVR ejere og koncern_struktur-data>",
    "ubo_analyse": "<2-3 sætninger: hvem er den ultimative beneficial owner? Beskriv ejerkæden fra koncern_struktur>",
    "koncernkort": [
      {{"niveau": "<niveau, 0=UBO, 1=direkte ejer, 2=selskabet selv>", "enhed": "<enhed>", "cvr": "<cvr eller null>", "type": "<Person|ApS|A/S|etc.>", "ejerandel": "<andel>", "status": "<NORMAL|OPLØST|etc.>", "bemaerkning": "<bemærkning>"}}
    ],
    "kompleksitet_risiko": "<'lav' hvis enkel struktur ≤2 niveauer; 'middel' hvis 3 niveauer; 'høj' hvis >3 niveauer eller ukendte ejere>",
    "revisor_anbefaling": "<anbefaling>"
  }},

  "sektion_10": {{
    "adresse_skift": [
      {{"dato": "<YYYY-MM-DD>", "beskrivelse": "<hvad der skete>"}}
    ],
    "navn_skift": [
      {{"dato": "<YYYY-MM-DD>", "beskrivelse": "<navneændring>"}}
    ],
    "direktor_skift": [
      {{"dato": "<YYYY-MM-DD>", "beskrivelse": "<hvad der skete med ledelsen>"}}
    ],
    "roede_flag": ["<rødt flag fra historik_data.roede_flag — kopieres direkte>"],
    "samlet_vurdering": "<1-2 sætninger — stabil historik eller mange skift?>",
    "risiko": "<'lav'|'middel'|'høj' — baseret på antal skift og røde flag>"
  }},

  "sektion_11": {{
    "fund": [
      {{"dato": "<dato>", "overskrift": "<overskrift>", "kilde": "Statstidende.dk", "beskrivelse": "<kort beskrivelse>"}}
    ],
    "risiko": "<kopieres fra retssager.risiko: 'lav'|'middel'|'høj'>",
    "samlet_vurdering": "<'Ingen fund i Statstidende.dk' eller beskrivelse af fund og deres betydning>"
  }},

  "sektion_04": {{
    "ingen_regnskaber_note": null,
    "aar_kolonner": ["<seneste år YYYY>", "<år-1>", "<år-2>", "<år-3>", "<år-4>"],
    "regnskabspost_tabel": [
      {{"post": "<post>", "vaerdier": ["<beløb seneste>", "<beløb år-1>", "<beløb år-2>", "<beløb år-3>", "<beløb år-4>"], "enhed": "t.DKK"}}
    ],
    "finansiel_analyse": "<3-5 sætninger om finansiel situation og trend over perioden>",
    "noegletal_tabel": [
      {{"noegletal": "<navn>", "selskab": "<værdi>", "branche_median": "<værdi>", "vurdering": "lav|middel|høj|ukendt"}}
    ],
    "paategninger": {{
      "historik": [
        {{"aar": "<år YYYY>", "paategning": "<påtegningstype>", "revisor": "<revisorens navn eller 'Ukendt'>"}}
      ]
    }}
  }},

  "sektion_05": {{
    "kreditpunkter": [
      {{"punkt": "<punkt>", "vaerdi": "<værdi>", "vurdering": "lav|middel|høj|ukendt"}}
    ],
    "kreditnote": "Denne rapport inkluderer ikke en formel kreditscoring. Revisor anbefales at indhente formel kreditrapport via Experian DK eller Dun & Bradstreet ved behov."
  }},

  "sektion_06": {{
    "soegning_dato": "{today}",
    "soegeresultater": [
      {{"omraade": "<område>", "resultat": "<resultat>", "kilde": "<kilde>"}}
    ],
    "positive_observationer": "<2-3 sætninger om positive fund>",
    "trustpilot": {{
      "rating": "<samlet rating fra trustpilot-data, eller null>",
      "antal": "<antal anmeldelser, eller null>",
      "url": "<url fra trustpilot-data, eller null>",
      "vurdering": "<'lav'|'middel'|'høj'|'ingen_profil' — baseret på rating og indhold>",
      "resumé": "<1-2 sætninger om hvad anmeldelserne samlet siger, eller 'Ingen Trustpilot-profil fundet'>",
      "roede_flag": ["<evt. konkrete røde flag fra anmeldelserne, f.eks. 'Flere anmeldelser nævner manglende refusion'>"],
      "udvalgte_anmeldelser": [
        {{"stjerner": "<1-5>", "titel": "<titel>", "tekst": "<kort uddrag>", "dato": "<YYYY-MM-DD>"}}
      ]
    }}
  }},

  "sektion_07": {{
    "branchekode_db07": "<kode — beskrivelse>",
    "nace_kode": "<nace>",
    "branchekarakter": "<karakter>",
    "typisk_kundesegment": "<segment>",
    "markedssituation": "<1-2 sætninger>",
    "selskab_navn": "<navn fra CVR-data — bruges som kolonneoverskrift i benchmarking>",
    "benchmarking": [
      {{"noegletal": "<navn>", "branche_median": "<branchegennemsnit>", "selskab": "<virksomhedens egen værdi fra regnskabsdata eller null>", "vurdering": "lav|middel|høj|ukendt"}}
    ],
    "benchmarking_note": "Branchemedian baseret på offentligt tilgængeligt materiale. Selskabets egne tal fra seneste indberettede årsrapport."
  }},

  "sektion_08": {{
    "ledelsesprofiler": [
      {{
        "navn": "<navn fra CVR ledelse>",
        "titel": "<titel>",
        "punkter": ["<punkt 1>", "<punkt 2>", "<punkt 3>"],
        "selskaber": [
          {{"navn": "<selskabsnavn>", "cvr": "<cvr>", "status": "<NORMAL|OPLØST|OPLØST EFTER KONKURS>", "rolle": "<rolle>", "periode": "<startår-slutår eller ->", "konkurs": false}}
        ]
      }}
    ],
    "anbefaling": "<anbefaling>"
  }},

  "sektion_09": {{
    "samlet_vurdering": "<2-3 sætninger>",
    "risiko_niveau": "lav|middel|høj",
    "risikohandlinger": [
      {{"kategori": "<kategori>", "risiko": "lav|middel|høj|ukendt", "handling": "<handling>"}}
    ],
    "ansvarsfraskrivelse": "Denne rapport er udarbejdet på baggrund af offentligt tilgængelige data fra CVR, søgemaskiner og branchedatabaser pr. {today}. Rapporten udgør ikke en juridisk due diligence, en komplet AML-analyse, eller en kreditfaglig vurdering. Revisor er ansvarlig for at indhente den nødvendige yderligere dokumentation i overensstemmelse med gældende lovgivning, herunder hvidvaskloven og revisorlovgivningen."
  }}
}}
"""


async def generate_report_data(cvr_data: dict) -> dict:
    client = _client()
    today = _danish_date()

    user_prompt = USER_PROMPT_TEMPLATE.format(
        cvr_json=json.dumps(cvr_data, ensure_ascii=False, indent=2),
        today=today,
    )

    # Bruger streaming — påkrævet af Anthropic SDK for lange requests (>10 min)
    # Hele kaldet er wrappet i try/except så Kurt får en forståelig besked
    # hvis Anthropic API'et er nede eller svarer langsomt.
    try:
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=24000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            chunks = []
            for text in stream.text_stream:
                chunks.append(text)
            message = stream.get_final_message()
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"Kunne ikke forbinde til Claude API. Tjek internetforbindelse. ({e})")
    except anthropic.RateLimitError as e:
        raise RuntimeError(f"Claude API er overbelastet lige nu. Prøv igen om 30 sekunder. ({e})")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude API fejl (status {e.status_code}). Prøv igen. ({e.message})")
    except Exception as e:
        raise RuntimeError(f"Uventet fejl under rapport-generering: {e}")

    raw = "".join(chunks).strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove first line (```json or ```) and last ``` if present
        raw = "\n".join(lines[1:])
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3].rstrip()

    # Verify we got complete JSON — if not, raise a clear error
    if not raw.endswith("}"):
        raise ValueError(
            f"Claude's svar blev afskåret (stop_reason={message.stop_reason}). "
            "Prøv igen."
        )

    # Parse JSON med fallback-fejlmeddelelse hvis det er ugyldigt
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Claude returnerede ugyldig JSON (position {e.pos}). "
            "Dette sker sjældent — prøv at generere rapporten igen."
        )


def _danish_date() -> str:
    months = ["januar","februar","marts","april","maj","juni",
              "juli","august","september","oktober","november","december"]
    d = date.today()
    return f"{d.day:02d}. {months[d.month-1]} {d.year}"
