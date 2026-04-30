"""
FastAPI backend — Revidera Due Diligence Platform.

Start:  uvicorn app:app --reload
"""

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import Response, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(_env_path, override=True)

from cvr import fetch_cvr_data
from report import generate_report_data
from pdf_gen import generate_pdf
from ktv_gen import generate_ktv
from excel_gen import fill_template, AVAILABLE_PLACEHOLDERS

# ── Dirs ──────────────────────────────────────────────────────────────────────
_BASE          = Path(__file__).resolve().parent
_TEMPLATE_DIR  = _BASE / ".templates"
_REPORTS_DIR   = _BASE / "reports"
_TEMPLATE_DIR.mkdir(exist_ok=True)
_REPORTS_DIR.mkdir(exist_ok=True)

_TEMPLATE_DATA     = _TEMPLATE_DIR / "template.xlsx"
_TEMPLATE_NAME     = _TEMPLATE_DIR / "template.name"
_TEMPLATE_ANALYSIS = _TEMPLATE_DIR / "template_analysis.json"


# ── Auth ──────────────────────────────────────────────────────────────────────

def _token_for(password: str) -> str:
    """Deterministisk token — samme kodeord giver altid samme token."""
    secret = os.environ.get("ANTHROPIC_API_KEY", "fallback")[:16]
    return hashlib.sha256(f"{password}:{secret}".encode()).hexdigest()


def _valid_cvr_checksum(cvr: str) -> bool:
    """Validér dansk CVR-nummer med officiel vægt-algoritme (mod 11)."""
    if len(cvr) != 8 or not cvr.isdigit():
        return False
    weights = [2, 7, 6, 5, 4, 3, 2, 1]
    total = sum(int(c) * w for c, w in zip(cvr, weights))
    return total % 11 == 0


def _valid_token(token: str) -> bool:
    pw = os.environ.get("ACCESS_PASSWORD", "")
    if not pw:
        return True          # intet kodeord sat → åben adgang
    return token == _token_for(pw)


async def require_auth(request: Request):
    """FastAPI dependency — kast 401 hvis token mangler/forkert."""
    pw = os.environ.get("ACCESS_PASSWORD", "")
    if not pw:
        return   # ingen password sat — åben adgang
    token = request.headers.get("X-Auth-Token", "")
    if not _valid_token(token):
        raise HTTPException(status_code=401, detail="Ikke autoriseret.")


# ── Template helpers ──────────────────────────────────────────────────────────

def _template_info() -> dict:
    if _TEMPLATE_DATA.exists() and _TEMPLATE_NAME.exists():
        return {"filename": _TEMPLATE_NAME.read_text(encoding="utf-8")}
    return {"filename": None}


def _template_bytes():
    return _TEMPLATE_DATA.read_bytes() if _TEMPLATE_DATA.exists() else None


# ── Report helpers ────────────────────────────────────────────────────────────

def _safe(name: str) -> str:
    return re.sub(r"[^\w\s-]", "", name).strip()[:40]


def _save_report(report_data: dict) -> str:
    meta         = report_data.get("meta", {})
    cvr          = meta.get("cvr", "ukendt")
    company      = _safe(meta.get("selskabsnavn", "ukendt"))
    ts           = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_id    = f"{ts}_{cvr}"
    path         = _REPORTS_DIR / f"{report_id}.json"
    path.write_text(json.dumps(report_data, ensure_ascii=False), encoding="utf-8")
    return report_id


def _list_reports() -> list:
    items = []
    for p in sorted(_REPORTS_DIR.glob("*.json"), reverse=True)[:50]:
        try:
            data   = json.loads(p.read_text(encoding="utf-8"))
            meta   = data.get("meta", {})
            s09    = data.get("sektion_09", {})
            risiko = s09.get("risiko_niveau", "")
            # Parse timestamp from filename: YYYYMMDD_HHMMSS_CVR
            parts  = p.stem.split("_")
            dato   = f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}" if len(parts[0]) == 8 else ""
            items.append({
                "id":          p.stem,
                "cvr":         meta.get("cvr", ""),
                "selskabsnavn": meta.get("selskabsnavn", p.stem),
                "dato":        dato,
                "risiko":      risiko,
            })
        except Exception:
            pass
    return items


# ── Job queue ─────────────────────────────────────────────────────────────────
# Gemmer igangværende og færdige jobs i hukommelsen.
# Jobs slettes automatisk efter 2 timer.

_JOBS: dict = {}   # job_id → {status, trin, result, error, created_at}


def _cleanup_jobs():
    """Fjern jobs ældre end 2 timer så hukommelsen ikke vokser."""
    cutoff = datetime.now() - timedelta(hours=2)
    stale = [jid for jid, j in _JOBS.items() if j["created_at"] < cutoff]
    for jid in stale:
        del _JOBS[jid]


async def _run_job(job_id: str, cvr: str):
    """Kører CVR-opslag + rapport-generering i baggrunden."""
    job = _JOBS[job_id]
    try:
        job["status"] = "running"
        job["trin"]   = "Henter virksomhedsdata fra CVR..."

        cvr_data = await fetch_cvr_data(cvr)

        job["trin"] = "Analyserer data og genererer rapport med AI..."

        report_data = await generate_report_data(cvr_data)
        report_data["cvr_data"] = cvr_data

        report_id = _save_report(report_data)
        report_data["_id"] = report_id

        job["status"] = "done"
        job["trin"]   = "Færdig"
        job["result"] = report_data

    except ValueError as e:
        job["status"] = "error"
        job["error"]  = str(e)
    except RuntimeError as e:
        job["status"] = "error"
        job["error"]  = str(e)
    except Exception as e:
        job["status"] = "error"
        job["error"]  = f"Uventet fejl: {type(e).__name__}: {e}"
    finally:
        _cleanup_jobs()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Revidera Due Diligence Platform")
app.mount("/static", StaticFiles(directory="frontend"), name="static")

AUTH = Depends(require_auth)


# ── Public ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return Path("frontend/index.html").read_text(encoding="utf-8")


@app.post("/api/auth/login")
async def login(body: dict):
    """Valider kodeord og returner token."""
    pw_env = os.environ.get("ACCESS_PASSWORD", "")
    if not pw_env:
        return JSONResponse({"token": "no-auth"})
    pw = body.get("password", "")
    if not pw or hashlib.sha256(pw.encode()).hexdigest()[:8] == "x":   # dummy check path
        pass
    if pw != pw_env:
        raise HTTPException(status_code=401, detail="Forkert kodeord.")
    return JSONResponse({"token": _token_for(pw)})


@app.get("/api/auth/check")
async def auth_check():
    """Fortæl frontend om der kræves kodeord."""
    return JSONResponse({"requires_password": bool(os.environ.get("ACCESS_PASSWORD", ""))})


@app.get("/api/health")
async def health():
    """Simpel status-check — bruges af Railway + til at verificere at serveren kører."""
    return JSONResponse({
        "status": "ok",
        "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY", "")),
        "has_password": bool(os.environ.get("ACCESS_PASSWORD", "")),
    })


# ── Beskyttet: rapporter ──────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    cvr: str


@app.post("/api/generate", dependencies=[AUTH])
async def generate(req: GenerateRequest):
    """Starter rapport-generering som baggrundsjob og returnerer job_id straks."""
    cvr = req.cvr.replace(" ", "").strip()
    if len(cvr) != 8 or not cvr.isdigit():
        raise HTTPException(status_code=400, detail="CVR-nummer skal være 8 cifre.")
    if not _valid_cvr_checksum(cvr):
        raise HTTPException(status_code=400, detail=f"CVR-nummer {cvr} er ugyldigt (fejl i kontrolciffer).")

    job_id = str(uuid.uuid4())
    _JOBS[job_id] = {
        "status":     "pending",
        "trin":       "Starter op...",
        "result":     None,
        "error":      None,
        "created_at": datetime.now(),
    }

    # Start jobbet i baggrunden — returnér straks job_id til frontend
    asyncio.create_task(_run_job(job_id, cvr))

    return JSONResponse({"job_id": job_id})


@app.get("/api/jobs/{job_id}", dependencies=[AUTH])
async def get_job(job_id: str):
    """Poll job-status. Frontend kalder dette hvert 3. sekund."""
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job ikke fundet.")
    response = {
        "status": job["status"],
        "trin":   job["trin"],
        "error":  job["error"],
    }
    # Inkluder kun result når jobbet er færdigt
    if job["status"] == "done":
        response["result"] = job["result"]
    return JSONResponse(response)


@app.get("/api/reports", dependencies=[AUTH])
async def list_reports():
    return JSONResponse(_list_reports())


@app.get("/api/reports/{report_id}", dependencies=[AUTH])
async def get_report(report_id: str):
    path = _REPORTS_DIR / f"{report_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Rapport ikke fundet.")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["_id"] = report_id
    return JSONResponse(data)


@app.delete("/api/reports/{report_id}", dependencies=[AUTH])
async def delete_report(report_id: str):
    path = _REPORTS_DIR / f"{report_id}.json"
    path.unlink(missing_ok=True)
    return JSONResponse({"ok": True})


# ── Beskyttet: downloads ──────────────────────────────────────────────────────

@app.post("/api/ktv", dependencies=[AUTH])
async def download_ktv(report_data: dict):
    try:
        pdf_bytes = generate_ktv(report_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"KTV-generering fejlede: {e}")
    meta       = report_data.get("meta", {})
    safe_name  = _safe(meta.get("selskabsnavn", "rapport"))
    cvr        = meta.get("cvr", "")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="KTV_{safe_name}_{cvr}.pdf"'})


@app.post("/api/pdf", dependencies=[AUTH])
async def download_pdf(report_data: dict):
    try:
        pdf_bytes = generate_pdf(report_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF-generering fejlede: {e}")
    meta       = report_data.get("meta", {})
    safe_name  = _safe(meta.get("selskabsnavn", "rapport"))
    cvr        = meta.get("cvr", "")
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="Due_Diligence_{safe_name}_{cvr}.pdf"'})


# ── Beskyttet: skabelon ───────────────────────────────────────────────────────

@app.get("/api/template", dependencies=[AUTH])
async def get_template_info():
    return JSONResponse(_template_info())


@app.post("/api/template/upload", dependencies=[AUTH])
async def upload_template(file: UploadFile = File(...)):
    from template_ai import analyze_template as _analyze
    ext = Path(file.filename).suffix.lower()
    if ext not in (".xlsx", ".pdf"):
        raise HTTPException(status_code=400, detail="Kun .xlsx og .pdf filer er understøttet.")
    data = await file.read()
    try:
        analysis = await _analyze(data, file.filename)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Skabelon-analyse fejlede: {e}")
    _TEMPLATE_DATA.write_bytes(data)
    _TEMPLATE_NAME.write_text(file.filename, encoding="utf-8")
    _TEMPLATE_ANALYSIS.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
    mapping  = analysis.get("mapping", {})
    detected = ([f for sheet in mapping.values() for f in sheet.values()]
                if analysis["type"] == "xlsx"
                else [i["field"] for i in mapping.get("felt_map", [])])
    return JSONResponse({"filename": file.filename, "type": analysis["type"], "detected_fields": detected})


@app.delete("/api/template", dependencies=[AUTH])
async def delete_template():
    _TEMPLATE_DATA.unlink(missing_ok=True)
    _TEMPLATE_NAME.unlink(missing_ok=True)
    _TEMPLATE_ANALYSIS.unlink(missing_ok=True)
    return JSONResponse({"ok": True})


@app.post("/api/excel", dependencies=[AUTH])
async def download_excel(report_data: dict):
    from template_ai import fill_from_mapping
    tpl = _template_bytes()
    if tpl is None:
        raise HTTPException(status_code=400, detail="Ingen skabelon uploadet endnu.")
    if not _TEMPLATE_ANALYSIS.exists():
        raise HTTPException(status_code=400, detail="Skabelon-analyse mangler. Upload skabelonen igen.")
    analysis = json.loads(_TEMPLATE_ANALYSIS.read_text(encoding="utf-8"))
    try:
        filled = fill_from_mapping(tpl, analysis, report_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel-generering fejlede: {e}")
    meta      = report_data.get("meta", {})
    safe_name = _safe(meta.get("selskabsnavn", "rapport"))
    cvr       = meta.get("cvr", "")
    return Response(content=filled,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{safe_name}_{cvr}.xlsx"'})


@app.post("/api/template/preview", dependencies=[AUTH])
async def preview_template(report_data: dict):
    from template_ai import preview_mapping
    if not _TEMPLATE_ANALYSIS.exists():
        raise HTTPException(status_code=400, detail="Ingen skabelon-analyse fundet.")
    analysis = json.loads(_TEMPLATE_ANALYSIS.read_text(encoding="utf-8"))
    return JSONResponse(preview_mapping(analysis, report_data))


@app.get("/api/placeholders", dependencies=[AUTH])
async def list_placeholders():
    return JSONResponse(AVAILABLE_PLACEHOLDERS)
