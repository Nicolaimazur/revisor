"""
FastAPI backend — Revisor Due Diligence Platform.

Start:  uvicorn app:app --reload
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
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


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Revisor Due Diligence Platform")
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


# ── Beskyttet: rapporter ──────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    cvr: str


@app.post("/api/generate", dependencies=[AUTH])
async def generate(req: GenerateRequest):
    cvr = req.cvr.replace(" ", "").strip()
    if len(cvr) != 8 or not cvr.isdigit():
        raise HTTPException(status_code=400, detail="CVR-nummer skal være 8 cifre.")
    try:
        cvr_data = await fetch_cvr_data(cvr)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"CVR-opslag fejlede: {e}")
    try:
        report_data = await generate_report_data(cvr_data)
    except EnvironmentError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Rapport-generering fejlede: {e}")

    # Inkluder rå CVR-data (koncernstruktur, historik, retssager, pep_screening)
    # så frontend kan rendere visuelt træ og supplere AI-sektionerne
    report_data["cvr_data"] = cvr_data

    report_id = _save_report(report_data)
    report_data["_id"] = report_id
    return JSONResponse(content=report_data)


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
