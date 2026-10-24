"""
Daemon de captura + conciliación: Dictados → Fortemi ⇄ KOReader
Watcher + uploader + waiter + conciliador de sidecars.
La cascada es de Fortemi. Paralelizado con ThreadPoolExecutor.

Pipeline de audios (igual que siempre):
  1. Poll /dictados/
  2. ffprobe: duración, formato. Si <mínimo: cortos. Si 3GPP: re-mux M4A.
  3. POST /notes {pipeline:[], revision_mode:"none", document_type:"voice-memo-semantic"}
  4. SQL UPDATE created_at_utc
  5. POST /notes/{id}/attachments/upload
  6. wait_for_jobs (sin timeout, Fortemi maneja con JOB_TIMEOUT_SECS)
  7. GET /jobs?status=failed — anotar cuáles fallaron
  8. Book detection tag-only (el quotation inferencial está jubilado)
  9. POST /notes/{id}/reprocess {steps, revision_mode:"full"}
  10. wait_for_jobs
  11. Mover a procesados
  12. state=completed
  13. Conciliación: este audio puede llenar un highlight en espera

Conciliación de sidecars:
  S1. Poll /libros-sync/**.sdr/metadata.*.lua (mtime+size vs baseline)
  S2. Cambio = libro cerrado en algún dispositivo → conciliar:
      - highlight nuevo con nota manual → nota en Fortemi  [KOReader → Fortemi]
      - highlight nuevo sin nota → awaiting_audio
      - filled/failed_write pero sin note en archivo → pisado → reintento
        (cap WRITE_CAP; failed_write revive tras cooldown)
  S3. Audios completos no asignados vs highlights elegibles:
      ventana 5 min + LIFO → transcripción al note del highlight
      [Fortemi → KOReader] + tag del libro y cita en la nota del audio
  S4. El propio write-back cambia el .sdr → el watcher re-dispara → no-op
      (autocuración). Si KOReader pisa (libro abierto al llegar la nota),
      cada pisada es un cambio real → reintento con cap y cooldown.
"""

import os
import re
import json
import time
import shutil
import logging
import argparse
import unicodedata
import threading
import psycopg2
import requests
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor

from tinfoil_client import detectar_libro
from book_search import encontrar_libro, escanear_libros

import sidecars
import conciliacion

# ─── Config ───
DICTADOS_DIR = Path("/dictados")
AUDIOS_PROCESADOS_DIR = Path("/protesis/audios-procesados")
AUDIOS_CORTOS_DIR = Path("/protesis/audios-cortos")
STATE_DIR = Path("/protesis/estado")
STATE_FILE = STATE_DIR / "state.json"
LIBROS_DIR = Path("/libros")

# conciliación de sidecars
LIBROS_SYNC_DIR = Path(os.environ.get("LIBROS_SYNC_DIR", "/libros-sync"))
SIDECAR_STATE_FILE = STATE_DIR / "sidecars.json"      # baseline mtime+size por .lua
SIDECAR_BACKUP_DIR = STATE_DIR / "sidecar_backups"    # backups de write-back
VENTANA_MINUTOS = int(os.environ.get("VENTANA_MINUTOS", "5"))
NO_RETROACTIVIDAD = datetime(2026, 10, 1)  # hora local; antes de aquí → done_empty
POLL_INTERVAL_SIDECARS = int(os.environ.get("POLL_INTERVAL_SIDECARS", "10"))
WRITE_CAP = 3  # reintentos de write-back antes de failed_write
FAILED_WRITE_COOLDOWN_MIN = 30  # minutos antes de revivir un failed_write

FORTEMI_API = "http://matric-api:3000/api/v1"
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = "matric"
POSTGRES_USER = "matric"
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "matric")

EXTENSIONES_AUDIO = {".aac", ".m4a", ".mp3", ".wav", ".ogg", ".amr"}
POLL_INTERVAL_AUDIOS = 10
POLL_INTERVAL_LIBROS = 60
DURACION_MINIMA = float(os.environ.get("DURACION_MINIMA", "3.0"))
STATE_MAX_ENTRIES = 1000
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "3"))

REPROCESS_STEPS = ["metadata_extraction", "reference_extraction", "ai_revision"]

# zona de los dispositivos que graban: la hora de los filenames es hora local de aquí
TZ_DISPOSITIVO = ZoneInfo(os.environ.get("TZ_DISPOSITIVO", "Europe/Madrid"))

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── Thread-safe state ───
state_lock = threading.Lock()

# locks de conciliación
_pendientes_lock = threading.Lock()
_sidecar_locks = {}
_sidecar_locks_guard = threading.Lock()
_sidecar_state = {}
_sidecar_state_lock = threading.Lock()

# ─── DB (set_timestamp y cita de highlight) ───
def db_query(query, params=None):
    conn = psycopg2.connect(
        host=POSTGRES_HOST, port=POSTGRES_PORT,
        dbname=POSTGRES_DB, user=POSTGRES_USER,
        password=POSTGRES_PASSWORD
    )
    try:
        cur = conn.cursor()
        cur.execute(query, params or ())
        result = cur.fetchall() if cur.description else []
        conn.commit()
        return result
    finally:
        conn.close()

def set_timestamp(note_id, timestamp):
    db_query(
        "UPDATE note SET created_at_utc = %s WHERE id = %s",
        (timestamp, note_id)
    )

def append_cita_libro(note_id, cita, title_author, chapter="", pageno=None):
    """Cita con contexto del highlight (capítulo/página vienen del sidecar).
    Formato del daemon conservado + contexto."""
    contexto = ""
    if chapter:
        contexto += f", cap. {chapter}"
    if pageno is not None:
        contexto += f", pág. {pageno}"
    db_query(
        "UPDATE note_original SET content = content || %s WHERE note_id = %s",
        (f"\n\n> {cita}\n> — *{title_author}*{contexto}", note_id)
    )


# ─── Fortemi API ───
def create_note():
    r = requests.post(f"{FORTEMI_API}/notes", json={
        "content": "",
        "pipeline": [],
        "revision_mode": "none",
        "document_type": "voice-memo-semantic"
    }, timeout=30)
    r.raise_for_status()
    return r.json()["id"]

def create_text_note(content):
    """Nota de texto (highlight manual → Fortemi). Sin document_type:
    el reprocess lo infiere (document_type_inference)."""
    r = requests.post(f"{FORTEMI_API}/notes", json={
        "content": content,
        "pipeline": [],
        "revision_mode": "none"
    }, timeout=30)
    r.raise_for_status()
    return r.json()["id"]

def upload_attachment(note_id, audio_path):
    with open(audio_path, "rb") as f:
        r = requests.post(
            f"{FORTEMI_API}/notes/{note_id}/attachments/upload",
            files={"file": (audio_path.name, f)},
            timeout=600
        )
    if r.status_code == 400:
        raise Exception(f"Upload 400: {r.text[:200]}")
    r.raise_for_status()

def get_note(note_id):
    r = requests.get(f"{FORTEMI_API}/notes/{note_id}", timeout=30)
    r.raise_for_status()
    return r.json()

def get_transcription(note_id):
    note = get_note(note_id)
    content = note.get("original", {}).get("content", "")
    return content.strip() if content else ""

def set_tags(note_id, tags):
    r = requests.put(
        f"{FORTEMI_API}/notes/{note_id}/tags",
        json={"tags": tags},
        timeout=30
    )
    r.raise_for_status()

def reprocess(note_id, steps=None, revision_mode="full"):
    if steps is None:
        steps = REPROCESS_STEPS
    r = requests.post(
        f"{FORTEMI_API}/notes/{note_id}/reprocess",
        json={"steps": steps, "revision_mode": revision_mode},
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def get_jobs(note_id):
    for attempt in range(3):
        try:
            r = requests.get(
                f"{FORTEMI_API}/jobs?note_id={note_id}",
                timeout=10
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                return data.get("jobs", data.get("data", []))
            return data
        except Exception as e:
            if attempt == 2:
                raise
            log.warning("get_jobs attempt %d failed: %s", attempt + 1, e)
            time.sleep(5)

def create_job(job_type, note_id):
    r = requests.post(
        f"{FORTEMI_API}/jobs",
        json={"job_type": job_type, "note_id": note_id},
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def wait_for_jobs(note_id, label=""):
    start = time.time()
    while True:
        jobs = get_jobs(note_id)
        pending = [j for j in jobs if j.get("status") in ("pending", "running")]
        if not pending:
            failed = [j for j in jobs if j.get("status") == "failed"]
            if failed:
                log.info("[%s] %s: %d failed jobs: %s",
                         note_id, label, len(failed),
                         [j.get("job_type") for j in failed])
            else:
                log.info("[%s] %s: completed", note_id, label)
            return failed
        elapsed = int(time.time() - start)
        if elapsed > 0 and elapsed % 60 == 0:
            log.info("[%s] Waiting for %s... (%ds, %d jobs: %s)",
                     note_id, label, elapsed, len(pending),
                     [j.get("job_type") for j in pending])
        time.sleep(2)


# ─── Audio helpers ───
def get_duration(audio_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", str(audio_path)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return None
        return float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except Exception:
        return None

def preparar_audio(audio_path):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_format", "-of", "json", str(audio_path)],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        return audio_path
    fmt = json.loads(result.stdout).get("format", {}).get("format_name", "")
    if "3gp" not in fmt:
        return audio_path
    tmp_path = Path("/tmp") / f"{audio_path.stem}.m4a"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-nostats", "-loglevel", "error",
             "-i", str(audio_path), "-c:a", "copy", str(tmp_path)],
            check=True, timeout=60
        )
        return tmp_path
    except subprocess.CalledProcessError:
        pass
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-nostats", "-loglevel", "error",
             "-i", str(audio_path), "-c:a", "aac", "-b:a", "128k", str(tmp_path)],
            check=True, timeout=300
        )
        return tmp_path
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        tmp_path.unlink(missing_ok=True)
        return audio_path

def parse_timestamp(filename):
    m = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})', filename)
    if m:
        naive = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H-%M-%S")
        local = naive.replace(tzinfo=TZ_DISPOSITIVO)
        return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None

def file_mtime(path):
    ts = path.stat().st_mtime
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

def wait_transfer(path):
    prev_size = -1
    for _ in range(20):
        try:
            curr = path.stat().st_size
        except FileNotFoundError:
            return False
        if curr == prev_size and curr > 0:
            return True
        prev_size = curr
        time.sleep(2)
    return False

def sanitize_tag(text):
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    result = re.sub(r'[^a-zA-Z0-9_/-]', '-', text)
    result = re.sub(r'-+', '-', result)
    return f"book/{result.strip('-')}"


# ─── Book detection (tag-only: quotation jubilado) ───
def process_book_detection(note_id, transcription, catalogo_libros):
    """Detección de libro solo para el tag. Sin cita, sin embeddings:
    el quotation inferencial está jubilado (cajas negras 03/04)."""
    if not transcription or not transcription.strip():
        return None
    try:
        resultado = detectar_libro(transcription, catalogo_libros)
    except Exception as e:
        log.warning("Book detection failed: %s", e)
        return None
    libro = resultado.get("libro_referenciado", "").strip()
    if not libro:
        log.info("[%s] No book detected", note_id)
        return None
    libro_path = None
    for root in (LIBROS_DIR, LIBROS_SYNC_DIR):
        if root.exists():
            libro_path = encontrar_libro(libro, root)
            if libro_path:
                break
    tag = sanitize_tag(libro_path.stem) if libro_path else sanitize_tag(libro)
    try:
        note_data = get_note(note_id)
        existing_tags = note_data.get("tags", [])
        if tag not in existing_tags:
            set_tags(note_id, existing_tags + [tag])
            log.info("Tag set: %s", tag)
    except Exception as e:
        log.warning("Tag failed: %s", e)
    return libro


# ─── State ───
def load_state():
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            log.warning("state.json corrupt, starting fresh")
    return {}

def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if len(state) > STATE_MAX_ENTRIES:
        state = maybe_rotate_state(state)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def save_state_locked(state):
    with state_lock:
        save_state(state)

def maybe_rotate_state(state):
    terminal = {"completed", "failed", "skipped", "timeout"}
    archived = {k: v for k, v in state.items() if v.get("status") in terminal}
    active = {k: v for k, v in state.items() if v.get("status") not in terminal}
    if not archived:
        return state
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    archive_path = STATE_DIR / f"state-{timestamp}.json"
    if archive_path.exists():
        with open(archive_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        existing.update(archived)
        archived = existing
    with open(archive_path, "w", encoding="utf-8") as f:
        json.dump(archived, f, indent=2, ensure_ascii=False)
    log.info("State rotated: %d archived, %d active", len(archived), len(active))
    return active

def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ════════════════════════════════════════════════════════════
# SIDECARS Y CONCILIACIÓN
# ════════════════════════════════════════════════════════════

def lock_sidecar(path_str):
    with _sidecar_locks_guard:
        if path_str not in _sidecar_locks:
            _sidecar_locks[path_str] = threading.Lock()
        return _sidecar_locks[path_str]


def load_sidecar_state():
    global _sidecar_state
    if SIDECAR_STATE_FILE.exists():
        try:
            with open(SIDECAR_STATE_FILE, "r", encoding="utf-8") as f:
                _sidecar_state = json.load(f)
        except (json.JSONDecodeError, IOError):
            log.warning("sidecars.json corrupt, baseline desde cero")
            _sidecar_state = {}
    else:
        _sidecar_state = {}


def actualizar_baseline_sidecar(sdr_path):
    try:
        st = sdr_path.stat()
    except OSError:
        return
    with _sidecar_state_lock:
        _sidecar_state[str(sdr_path)] = {"mtime": st.st_mtime, "size": st.st_size}
        tmp = SIDECAR_STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(_sidecar_state, indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(SIDECAR_STATE_FILE)


def get_changed_sidecars():
    out = []
    with _sidecar_state_lock:
        for sdr in sidecars.buscar_sidecars(LIBROS_SYNC_DIR):
            try:
                st = sdr.stat()
            except OSError:
                continue
            prev = _sidecar_state.get(str(sdr))
            if not prev or prev.get("mtime") != st.st_mtime or prev.get("size") != st.st_size:
                out.append(sdr)
    return out


def info_del_libro(data, sdr_path):
    doc_props = data.get("doc_props") or {}
    title = (doc_props.get("title") or "").strip()
    if not title:
        title = Path(sdr_path).parent.name.removesuffix(".sdr")
    authors = (doc_props.get("authors") or "").strip()
    md5 = data.get("partial_md5_checksum") or ""
    return {"md5": md5, "title": title, "authors": authors,
            "sdr_path": str(sdr_path)}


def title_author_del_libro(book):
    if book["authors"]:
        return f'{book["title"]} - {book["authors"]}'
    return book["title"]


# ─── Audios disponibles (activos + rotados) ───

_archivados_cache = None  # (frozenset_archivos, dict_merged)


def _archivos_rotados():
    global _archivados_cache
    files = frozenset(STATE_DIR.glob("state-*.json"))
    if _archivados_cache is None or _archivados_cache[0] != files:
        merged = {}
        for f in sorted(files):
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                for k, v in d.items():
                    if k not in merged:
                        merged[k] = v
            except Exception:
                continue
        _archivados_cache = (files, merged)
    return _archivados_cache[1]


def _parse_ts_utc(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def audios_disponibles(state):
    out = []
    for fname, e in state.items():
        ts = _parse_ts_utc(e.get("timestamp"))
        if e.get("status") == "completed" and e.get("note_id") and ts:
            out.append({"filename": fname, "ts": ts, "note_id": e["note_id"]})
    for fname, e in _archivos_rotados().items():
        if fname in state:
            continue
        ts = _parse_ts_utc(e.get("timestamp"))
        if e.get("status") == "completed" and e.get("note_id") and ts:
            out.append({"filename": fname, "ts": ts, "note_id": e["note_id"]})
    return out


# ─── Write-back al sidecar (Fortemi → KOReader) ───

def _escribir_note_en(sdr_path, book, key, transcripcion):
    """Relee el .sdr FRESH, localiza la anotación por identidad, escribe note.
    La nota manual previa (si existe) se conserva por concatenación — el borde."""
    data, header = sidecars.leer_sidecar(sdr_path)
    anns_key, anns = sidecars.anotaciones_de(data)
    if not anns_key:
        return False
    target = None
    for idx, a in anns.items():
        if isinstance(a, dict) and conciliacion.key_para(book["md5"], a) == key:
            target = a
            break
    if target is None:
        return False  # la anotación fue borrada
    target["note"] = conciliacion.componer_note(target.get("note", ""), transcripcion)
    target["datetime_updated"] = datetime.now(TZ_DISPOSITIVO).strftime("%Y-%m-%d %H:%M:%S")
    data["annotations_externally_modified"] = True
    bak = sidecars.escribir_sidecar(sdr_path, data, header, SIDECAR_BACKUP_DIR)
    log.info("Write-back OK: %s (backup: %s)", key, bak.name)
    return True


def enriquecer_nota_audio(note_id, entry):
    """La nota del audio en Fortemi recibe el tag del libro y la cita del
    highlight (con capítulo/página) — reemplaza al quotation inferencial."""
    book, hl = entry["book"], entry["hl"]
    ta = title_author_del_libro(book)
    tag = sanitize_tag(ta)
    note_data = get_note(note_id)
    existing = note_data.get("tags", [])
    if tag not in existing:
        set_tags(note_id, existing + [tag])
    current = get_transcription(note_id)
    if ta in (current or ""):
        return  # ya enriquecida
    append_cita_libro(note_id, hl.get("text", ""), ta,
                      hl.get("chapter", "") or "", hl.get("pageno"))


def crear_nota_manual(annotation, book):
    """KOReader → Fortemi: highlight con nota manual → nota en Fortemi."""
    note_id = create_text_note(annotation.get("note", ""))
    append_cita_libro(note_id, annotation.get("text", ""),
                      title_author_del_libro(book),
                      annotation.get("chapter", "") or "", annotation.get("pageno"))
    tag = sanitize_tag(title_author_del_libro(book))
    existing = get_note(note_id).get("tags", [])
    if tag not in existing:
        set_tags(note_id, existing + [tag])
    reprocess(note_id)  # asíncrono: mismo tratamiento que los audios
    return note_id


# ─── El cruce: audios pendientes × highlights elegibles ───

def procesar_audios_pendientes(state):
    """Se llama al completar un audio y al conciliar un sidecar.
    Cada audio no asignado se cruza contra los highlights elegibles
    (awaiting_audio, manual, filled) por ventana + LIFO global."""
    with _pendientes_lock:
        try:
            asignados = []
            with conciliacion.registro() as reg:
                elegibles = [(k, e) for k, e in reg["highlights"].items()
                             if e["state"] in ("awaiting_audio", "manual",
                                               "filled_from_fortemi")]
                if not elegibles:
                    return
                audios = audios_disponibles(state)
                libres = [a for a in audios
                          if a["filename"] not in reg["audios_asignados"]]
                for au in sorted(libres, key=lambda x: x["ts"]):
                    key = conciliacion.elegir_highlight(au["ts"], elegibles,
                                                        VENTANA_MINUTOS)
                    if key is None:
                        continue
                    reg["audios_asignados"][au["filename"]] = key
                    reg["highlights"][key]["audios"].append(au["filename"])
                    asignados.append((key, dict(reg["highlights"][key]), au))
            if not asignados:
                return
            for key, entry, au in asignados:
                try:
                    transcripcion = get_transcription(au["note_id"])
                except Exception as e:
                    log.warning("get_transcription falló: %s", e)
                    transcripcion = ""
                if not transcripcion:
                    # revertir asignación: el audio queda disponible para reintentos
                    with conciliacion.registro() as reg:
                        reg["audios_asignados"].pop(au["filename"], None)
                        hl = reg["highlights"].get(key)
                        if hl and au["filename"] in hl["audios"]:
                            hl["audios"].remove(au["filename"])
                    log.info("Audio sin transcripción aún, queda pendiente: %s",
                             au["filename"])
                    continue
                sdr_path = Path(entry["book"]["sdr_path"])
                ok = False
                if sdr_path.exists():
                    with lock_sidecar(str(sdr_path)):
                        ok = _escribir_note_en(sdr_path, entry["book"], key,
                                               transcripcion)
                with conciliacion.registro() as reg:
                    e2 = reg["highlights"].get(key)
                    if e2 is None:
                        continue
                    if ok:
                        e2["state"] = "filled_from_fortemi"
                        e2["attempts"] = 0
                    else:
                        e2["state"] = "done_empty"
                        e2["reason"] = "anotacion_borrada"
                if ok:
                    try:
                        enriquecer_nota_audio(au["note_id"], entry)
                    except Exception as e:
                        log.warning("Enriquecer nota de audio falló: %s", e)
                    if au["filename"] in state:
                        state[au["filename"]]["highlight"] = {
                            "book": entry["book"]["md5"], "key": key}
                        save_state_locked(state)
                    log.info("Audio %s → highlight %s", au["filename"], key)
                else:
                    log.info("Anotación ya no existe en el .sdr: %s", key)
        except Exception as e:
            log.error("procesar_audios_pendientes falló: %s", e)


# ─── Write-back pisado: reintento con cap y cooldown ───

def reintentar_write(key, book, sdr_path, state):
    """filled pero sin note en el archivo → KOReader pisó el write-back
    (el libro se abrió antes de que llegara la nota y al cerrar volcó su
    memoria). failed_write revive tras el cooldown: cada pisada es un cambio
    real del archivo, no un bucle autónomo."""
    with conciliacion.registro() as reg:
        e = reg["highlights"].get(key)
        if not e or e["state"] not in ("filled_from_fortemi", "failed_write"):
            return
        if e["state"] == "failed_write":
            last = _parse_ts_utc(e.get("last_attempt"))
            if last and (datetime.now(timezone.utc) - last) < timedelta(minutes=FAILED_WRITE_COOLDOWN_MIN):
                return  # aún en cooldown
            e["state"] = "filled_from_fortemi"
            e["attempts"] = 0
        if e["attempts"] >= WRITE_CAP:
            e["state"] = "failed_write"
            e["reason"] = "write-back pisado x%d" % WRITE_CAP
            e["last_attempt"] = now_iso()
            log.error("Write-back pisado %d veces (cap): %s", WRITE_CAP, key)
            return
        e["attempts"] += 1
        e["last_attempt"] = now_iso()
        intento = e["attempts"]
        audio_files = list(e["audios"])
        note_inicial = e["hl"].get("note_inicial", "")
    partes = []
    for fname in audio_files:
        ent = state.get(fname)
        if ent is None:
            ent = _archivos_rotados().get(fname)
        if ent and ent.get("note_id") and ent.get("status") == "completed":
            try:
                t = get_transcription(ent["note_id"])
                if t:
                    partes.append(t)
            except Exception:
                continue
    if not partes:
        log.warning("Reintento sin transcripciones: %s", key)
        return
    sdr_path = Path(sdr_path)
    with lock_sidecar(str(sdr_path)):
        data, header = sidecars.leer_sidecar(sdr_path)
        anns_key, anns = sidecars.anotaciones_de(data)
        target = None
        if anns_key:
            for idx, a in anns.items():
                if isinstance(a, dict) and conciliacion.key_para(book["md5"], a) == key:
                    target = a
                    break
        if target is None:
            with conciliacion.registro() as reg:
                e3 = reg["highlights"].get(key)
                if e3:
                    e3["state"] = "done_empty"
                    e3["reason"] = "anotacion_borrada"
            return
        target["note"] = conciliacion.componer_note(
            conciliacion.componer_note("", note_inicial), "\n\n".join(partes))
        target["datetime_updated"] = datetime.now(TZ_DISPOSITIVO).strftime(
            "%Y-%m-%d %H:%M:%S")
        data["annotations_externally_modified"] = True
        sidecars.escribir_sidecar(sdr_path, data, header, SIDECAR_BACKUP_DIR)
    with conciliacion.registro() as reg:
        e4 = reg["highlights"].get(key)
        if e4:
            e4["attempts"] = 0
    log.info("Write-back re-escrito tras pisado (intento %d/%d): %s",
             intento, WRITE_CAP, key)


# ─── Conciliar un sidecar (disparador: el .lua cambió) ───

def conciliar_libro(sdr_path, state, catalogo_libros):
    sdr_path = Path(sdr_path)
    try:
        if not wait_transfer(sdr_path):
            log.warning("Sidecar transfer incomplete: %s", sdr_path.name)
            return
        with lock_sidecar(str(sdr_path)):
            data, header = sidecars.leer_sidecar(sdr_path)
        anns_key, anns = sidecars.anotaciones_de(data)
        book = info_del_libro(data, sdr_path)
        if not book["md5"]:
            log.warning("[%s] sin partial_md5_checksum — conciliación omitida",
                        sdr_path.parent.name)
            actualizar_baseline_sidecar(sdr_path)
            return
        if not anns_key:
            actualizar_baseline_sidecar(sdr_path)
            return
        acciones = []
        with conciliacion.registro() as reg:
            for idx, a in anns.items():
                if not isinstance(a, dict):
                    continue
                key = conciliacion.key_para(book["md5"], a)
                entry = reg["highlights"].get(key)
                if entry is None:
                    if conciliacion.ya_visto(reg, book["md5"], a):
                        continue
                    dt_local = a.get("datetime")
                    if conciliacion.fue_preexistente(dt_local, NO_RETROACTIVIDAD):
                        conciliacion.agregar(reg, key, book, a, "done_empty",
                                             reason="pre-cutoff")
                        continue
                    dt_utc = sidecars.dt_local_a_utc_iso(dt_local, TZ_DISPOSITIVO)
                    if a.get("note"):
                        conciliacion.agregar(reg, key, book, a, "manual",
                                             dt_utc, VENTANA_MINUTOS)
                        acciones.append(("manual", key, dict(a)))
                    else:
                        conciliacion.agregar(reg, key, book, a, "awaiting_audio",
                                             dt_utc, VENTANA_MINUTOS)
                        log.info("[%s] highlight awaiting audio: %s",
                                 book["title"], dt_local)
                else:
                    if entry["state"] in ("filled_from_fortemi", "failed_write") and not a.get("note"):
                        acciones.append(("reintento", key, dict(a)))
                    elif (entry["state"] == "awaiting_audio"
                          and a.get("note")):
                        # el usuario añadió nota manual a un highlight en espera
                        entry["hl"]["note_inicial"] = a["note"]
                        entry["state"] = "manual"
                        acciones.append(("manual", key, dict(a)))
        for tipo, key, a in acciones:
            if tipo == "manual":
                try:
                    note_id = crear_nota_manual(a, book)
                    with conciliacion.registro() as reg:
                        e = reg["highlights"].get(key)
                        if e:
                            e["fortemi_note_id"] = note_id
                    log.info("[%s] nota manual → Fortemi: %s",
                             book["title"], note_id)
                except Exception as e:
                    log.error("Nota manual falló: %s", e)
                    with conciliacion.registro() as reg:
                        en = reg["highlights"].get(key)
                        if en:
                            en["state"] = "manual_failed"
                            en["reason"] = str(e)[:200]
            else:
                reintentar_write(key, book, sdr_path, state)
        actualizar_baseline_sidecar(sdr_path)
        procesar_audios_pendientes(state)
    except Exception as e:
        log.error("Conciliar %s falló: %s", sdr_path.name, e)


def inicializar_registro_highlights():
    """Primer arranque: todo lo que ya existe en disco → done_empty.
    Nada retroactivo; el sistema nace limpio."""
    items = []
    for sdr in sidecars.buscar_sidecars(LIBROS_SYNC_DIR):
        try:
            data, _ = sidecars.leer_sidecar(sdr)
            anns_key, anns = sidecars.anotaciones_de(data)
            if not anns_key:
                continue
            book = info_del_libro(data, sdr)
            if not book["md5"]:
                continue
            for idx, a in anns.items():
                if isinstance(a, dict) and a.get("datetime"):
                    items.append({
                        "key": conciliacion.key_para(book["md5"], a),
                        "md5": book["md5"],
                        "dt_local": a.get("datetime"),
                        "text": a.get("text", ""),
                        "book": book,
                        "ventana_min": VENTANA_MINUTOS,
                    })
        except Exception as e:
            log.warning("Inicializar %s falló: %s", sdr.name, e)
    n = conciliacion.inicializar(items, NO_RETROACTIVIDAD)
    log.info("Registro de highlights inicializado: %d preexistentes → done_empty", n)


# ─── Audio processing ───
def process_audio(audio_path, catalogo_libros, state):
    filename = audio_path.name

    if not audio_path.exists():
        log.warning("File not found: %s", filename)
        return

    # Init state
    if filename not in state or state[filename].get("status") != "processing":
        timestamp = parse_timestamp(filename) or file_mtime(audio_path)
        state[filename] = {
            "note_id": None,
            "status": "processing",
            "timestamp": timestamp,
            "errors": [],
            "step": "init",
            "processed_at": None,
        }
        save_state_locked(state)

    s = state[filename]
    note_id = s.get("note_id")
    timestamp = s.get("timestamp")

    # Duration check
    if "duration" not in s:
        s["duration"] = get_duration(audio_path)
        save_state_locked(state)
    duration = s.get("duration")

    if duration is not None and duration < DURACION_MINIMA:
        log.info("[%s] Duration %.1fs < %.1fs, skipping", filename, duration, DURACION_MINIMA)
        try:
            shutil.move(str(audio_path), str(AUDIOS_CORTOS_DIR / filename))
        except Exception as e:
            log.error("[%s] Move to cortos failed: %s", filename, e)
        s["status"] = "skipped"
        s["processed_at"] = now_iso()
        save_state_locked(state)
        return

    try:
        # Step 1: Create note
        if not note_id:
            note_id = create_note()
            s["note_id"] = note_id
            s["step"] = "note_created"
            save_state_locked(state)
            log.info("[%s] Note created: %s", filename, note_id)

        # Step 2: Set timestamp
        set_timestamp(note_id, timestamp)

        # Step 3: Upload audio
        if s.get("step") in ("init", "note_created"):
            upload_path = preparar_audio(audio_path)
            if upload_path != audio_path:
                log.info("[%s] Re-muxed to M4A", filename)
            upload_attachment(note_id, upload_path)
            if upload_path != audio_path and upload_path.exists():
                upload_path.unlink(missing_ok=True)
            s["step"] = "uploaded"
            save_state_locked(state)
            log.info("[%s] Audio uploaded", filename)

        # Step 4: Wait for cascada (extraction → ai_revision → ... → linking)
        if s.get("step") == "uploaded":
            failed = wait_for_jobs(note_id, "cascada")
            if failed:
                failed_types = [j.get("job_type") for j in failed]
                s["errors"].append(f"Cascada: {failed_types}")
                if "extraction" in failed_types:
                    s["status"] = "failed_extraction"
                    s["processed_at"] = now_iso()
                    save_state_locked(state)
                    log.error("[%s] Extraction failed: %s", filename, failed_types)
                    try:
                        shutil.move(str(audio_path), str(AUDIOS_PROCESADOS_DIR / filename))
                    except Exception as e:
                        log.error("[%s] Move failed: %s", filename, e)
                    return
                log.warning("[%s] Cascada had failures (proceeding): %s", filename, failed_types)
            else:
                log.info("[%s] Cascada OK", filename)
            s["step"] = "cascada_done"
            save_state_locked(state)

        # Step 5: Book detection (tag only — el quotation inferencial está jubilado)
        if s.get("step") == "cascada_done":
            transcription = get_transcription(note_id)
            if transcription:
                s["book_detected"] = process_book_detection(
                    note_id, transcription, catalogo_libros
                )
            else:
                log.warning("[%s] No transcription for book detection", filename)
            s["step"] = "book_detection_done"
            save_state_locked(state)

        # Step 6: Reprocess (metadata, reference, ai_revision full)
        if s.get("step") == "book_detection_done":
            reprocess(note_id)
            s["step"] = "reprocess_triggered"
            save_state_locked(state)
            log.info("[%s] Reprocess triggered", filename)

        # Step 7: Wait for reprocess
        if s.get("step") == "reprocess_triggered":
            failed = wait_for_jobs(note_id, "reprocess")
            if failed:
                failed_types = [j.get("job_type") for j in failed]
                s["errors"].append(f"Reprocess: {failed_types}")
                log.warning("[%s] Reprocess had failures: %s", filename, failed_types)
            else:
                log.info("[%s] Reprocess OK", filename)
            s["step"] = "done"
            save_state_locked(state)

        # Step 8: Finalize
        try:
            shutil.move(str(audio_path), str(AUDIOS_PROCESADOS_DIR / filename))
        except Exception as e:
            log.error("[%s] Move failed: %s", filename, e)
            s["errors"].append(f"Move: {str(e)[:200]}")

        s["status"] = "completed"
        s["processed_at"] = now_iso()
        save_state_locked(state)
        log.info("[%s] Completed", filename)

        # Step 9: este audio puede llenar un highlight en espera
        try:
            procesar_audios_pendientes(state)
        except Exception as e:
            log.warning("[%s] Conciliación post-audio falló: %s", filename, e)

    except Exception as e:
        s["status"] = "failed"
        s["errors"].append(str(e)[:200])
        s["processed_at"] = now_iso()
        save_state_locked(state)
        log.error("[%s] Failed: %s", filename, e)


# ─── Retry failed ───
def retry_failed(state):
    count = 0
    for filename, entry in list(state.items()):
        if entry.get("status") not in ("failed", "failed_extraction", "failed_cascade", "failed_reprocess", "timeout"):
            continue

        note_id = entry.get("note_id")
        if not note_id:
            log.warning("[%s] No note_id for retry", filename)
            continue

        log.info("[%s] Retrying (was %s)...", filename, entry.get("status"))

        try:
            jobs = get_jobs(note_id)
            failed = [j for j in jobs if j.get("status") == "failed"]
            failed_types = set(j.get("job_type") for j in failed)

            if "extraction" in failed_types:
                log.info("[%s] Re-triggering extraction", filename)
                create_job("extraction", note_id)
            elif failed_types:
                log.info("[%s] Re-triggering cascada via reprocess", filename)
                reprocess(note_id)
            else:
                log.info("[%s] No failed jobs, re-triggering reprocess", filename)
                reprocess(note_id)

            wait_for_jobs(note_id, "retry")

            jobs = get_jobs(note_id)
            failed = [j for j in jobs if j.get("status") == "failed"]
            if not failed:
                entry["status"] = "completed"
                entry["processed_at"] = now_iso()
                log.info("[%s] Retry succeeded", filename)
            else:
                entry["errors"].append(f"Retry: {[j.get('job_type') for j in failed]}")
                log.warning("[%s] Retry failed: %s", filename, [j.get("job_type") for j in failed])

        except Exception as e:
            entry["errors"].append(f"Retry: {str(e)[:200]}")
            log.error("[%s] Retry error: %s", filename, e)

        count += 1

    save_state_locked(state)
    return count


# ─── Scanning ───
def get_pending_audios(state):
    if not DICTADOS_DIR.exists():
        return []
    audios = []
    for ext in EXTENSIONES_AUDIO:
        audios.extend(DICTADOS_DIR.glob(f"*{ext}"))
    pending = []
    for audio in audios:
        if audio.name.startswith("."):
            continue
        if audio.name in state:
            status = state[audio.name].get("status")
            if status in ("completed", "failed", "skipped", "timeout",
                          "failed_extraction", "failed_cascade", "failed_reprocess"):
                continue
            pending.append(audio)
        else:
            if (AUDIOS_PROCESADOS_DIR / audio.name).exists():
                continue
            if (AUDIOS_CORTOS_DIR / audio.name).exists():
                continue
            pending.append(audio)
    pending.sort(key=lambda f: f.stat().st_mtime)
    return pending

def scan_libros(catalogo_libros):
    """Refresca el catálogo de títulos en AMBOS volúmenes (catálogo + estante).
    Sin embeddings: el quotation inferencial está jubilado y detectar_libro
    solo necesita títulos."""
    catalogo_libros.clear()
    for root in (LIBROS_DIR, LIBROS_SYNC_DIR):
        if root.exists():
            catalogo_libros.extend(escanear_libros(root))


# ─── Main ───
def main():
    parser = argparse.ArgumentParser(description="Daemon de captura + conciliación")
    parser.add_argument("--retry-failed", action="store_true",
                        help="Retry failed entries via API")
    args = parser.parse_args()

    AUDIOS_PROCESADOS_DIR.mkdir(parents=True, exist_ok=True)
    AUDIOS_CORTOS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()

    if args.retry_failed:
        count = retry_failed(state)
        log.info("Retried %d failed entries", count)
        return

    # conciliación: registro + baseline de sidecars
    conciliacion.configurar(STATE_DIR)
    load_sidecar_state()
    if not LIBROS_SYNC_DIR.exists():
        log.warning("Libros-Sync NO existe (%s) — conciliación de sidecars INACTIVA",
                    LIBROS_SYNC_DIR)
    else:
        with conciliacion.registro() as reg:
            ya_ini = reg.get("initialized", False)
        if not ya_ini:
            inicializar_registro_highlights()
        for sdr in sidecars.buscar_sidecars(LIBROS_SYNC_DIR):
            if str(sdr) not in _sidecar_state:
                actualizar_baseline_sidecar(sdr)

    catalogo_libros = []
    scan_libros(catalogo_libros)

    log.info("=" * 60)
    log.info("  Daemon iniciado.")
    log.info("  Dictados:       %s", DICTADOS_DIR)
    log.info("  Libros:         %s (catálogo)", LIBROS_DIR)
    log.info("  Libros-Sync:    %s", LIBROS_SYNC_DIR)
    log.info("  Procesados:     %s", AUDIOS_PROCESADOS_DIR)
    log.info("  Cortos:         %s", AUDIOS_CORTOS_DIR)
    log.info("  State:          %s", STATE_FILE)
    log.info("  Highlights:     %s", conciliacion.HIGHLIGHTS_FILE)
    log.info("  Libros en catalogo: %d", len(catalogo_libros))
    log.info("  Duracion min:   %.1fs", DURACION_MINIMA)
    log.info("  Max workers:    %d", MAX_WORKERS)
    log.info("  Ventana:        %d min · Corte: %s",
             VENTANA_MINUTOS, NO_RETROACTIVIDAD.date())
    log.info("=" * 60)

    last_libro_scan = 0.0
    last_sidecar_scan = 0.0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        active = {}  # {future: etiqueta}

        try:
            while True:
                if time.time() - last_libro_scan >= POLL_INTERVAL_LIBROS:
                    scan_libros(catalogo_libros)
                    last_libro_scan = time.time()

                # Limpiar futures completados
                completed = [(f, fn) for f, fn in active.items() if f.done()]
                for f, fn in completed:
                    del active[f]
                    try:
                        f.result()
                    except Exception as e:
                        log.error("[%s] Worker error: %s", fn, e)

                # sidecars cambiados → conciliar
                if (LIBROS_SYNC_DIR.exists()
                        and time.time() - last_sidecar_scan >= POLL_INTERVAL_SIDECARS):
                    last_sidecar_scan = time.time()
                    for sdr in get_changed_sidecars():
                        if len(active) >= MAX_WORKERS:
                            break
                        tag_name = f"sdr:{sdr.parent.name}/{sdr.name}"
                        if tag_name in active.values():
                            continue
                        future = executor.submit(conciliar_libro, sdr,
                                                 state, catalogo_libros)
                        active[future] = tag_name
                        log.info("Sidecar changed → conciliando: %s", sdr.name)

                # Submit nuevos audios si hay capacidad
                if len(active) < MAX_WORKERS:
                    pending = get_pending_audios(state)
                    processing_names = set(active.values())
                    for audio in pending:
                        if len(active) >= MAX_WORKERS:
                            break
                        if audio.name in processing_names:
                            continue
                        if wait_transfer(audio):
                            future = executor.submit(process_audio, audio, catalogo_libros, state)
                            active[future] = audio.name
                            processing_names.add(audio.name)
                            log.info("Submitted %s (active: %d/%d)",
                                     audio.name, len(active), MAX_WORKERS)
                        else:
                            log.warning("Transfer incomplete: %s", audio.name)

                if not active:
                    time.sleep(POLL_INTERVAL_AUDIOS)
                else:
                    time.sleep(2)

        except KeyboardInterrupt:
            log.info("Daemon detenido.")
            executor.shutdown(wait=True)


if __name__ == "__main__":
    main()