"""
conciliacion.py — La memoria de la conciliación: registro de highlights,
ventana de 5 minutos, LIFO con elegibilidad cronológica.

El registro (highlights.json) es la lista de "esto ya lo hice": una entrada
por highlight, con identidad pos0/pos1 + partial_md5 del libro, su estado
y sus audios asignados. Sin él, cada cierre de libro reprocesaría todo.

Estados:
  awaiting_audio      highlight sin nota, esperando audio en su ventana
  manual              highlight con nota manual del usuario → nota en Fortemi
  filled_from_fortemi transcripción escrita en el note del highlight
  done_empty          preexistente / pre-cutoff / anotación borrada — no se toca
  manual_failed       la nota manual no pudo crearse en Fortemi (log)
  failed_write        el write-back fue pisado WRITE_CAP veces (log)

Reglas verificadas contra las cajas negras 01–04:
  - ventana: audio ∈ [datetime_highlight, datetime_highlight + 5 min]
    (por INICIO de grabación; la duración no importa)
  - LIFO: un audio que califica a varios highlights va al de datetime
    MÁS RECIENTE que sea <= inicio del audio (global entre libros)
  - todo audio no asignado cuya hora caiga en la ventana pertenece:
    varios audios → todos al highlight, concatenados en orden
  - borde: nota manual + audio → concatenar

CLI:
  python3 conciliacion.py --test   # asserts sintéticos de ventana/LIFO/borde
"""

import json
import threading
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager


_lock = threading.Lock()
HIGHLIGHTS_FILE = None  # se fija con configurar()


def configurar(state_dir):
    global HIGHLIGHTS_FILE
    HIGHLIGHTS_FILE = Path(state_dir) / "highlights.json"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load():
    if HIGHLIGHTS_FILE is None:
        raise RuntimeError("conciliacion.configurar() no fue llamada")
    if HIGHLIGHTS_FILE.exists():
        try:
            with open(HIGHLIGHTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            # registro corrupto: no pisarlo en silencio — apartarlo y partir limpio
            corrupto = HIGHLIGHTS_FILE.with_name(
                HIGHLIGHTS_FILE.name + ".corrupt-" + now_iso().replace(":", ""))
            HIGHLIGHTS_FILE.rename(corrupto)
            print(f"highlights.json corrupto → {corrupto.name}", file=__import__('sys').stderr)
    return _default()


def _default():
    return {"initialized": False, "highlights": {}, "audios_asignados": {}, "by_dt_text": {}}


def _save(reg):
    tmp = HIGHLIGHTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(reg, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(HIGHLIGHTS_FILE)


@contextmanager
def registro():
    """Transacción atómica: cargar → mutar → guardar, bajo lock."""
    with _lock:
        reg = _load()
        yield reg
        _save(reg)


# ─── Identidad ───

def _pos_repr(p):
    if p is None:
        return "?"
    if isinstance(p, str):  # EPUB: XPointer
        return p
    if isinstance(p, dict):  # PDF: coords + zoom
        parts = []
        for k in ("page", "rotation", "x", "y", "zoom"):
            if k in p:
                v = p[k]
                parts.append(f"{k}:{v:.4f}" if isinstance(v, float) else f"{k}:{v}")
        return "|".join(parts)
    return str(p)


def key_para(md5, annotation):
    return f"{md5}::{_pos_repr(annotation.get('pos0'))}||{_pos_repr(annotation.get('pos1'))}"


def _k2(md5, annotation):
    return f"{md5}::{annotation.get('datetime')}::{annotation.get('text', '')}"


def ya_visto(reg, md5, annotation):
    """Dedupe secundario: mismo libro + mismo datetime + mismo texto = ya visto
    (defensa si pos0/pos1 variaran mínimamente entre re-dumps)."""
    return _k2(md5, annotation) in reg["by_dt_text"]


def agregar(reg, key, book, annotation, estado, dt_utc=None,
            ventana_min=5, reason=None):
    entry = {
        "book": {"md5": book["md5"], "title": book["title"],
                 "authors": book["authors"], "sdr_path": book["sdr_path"]},
        "hl": {
            "dt_local": annotation.get("datetime"),
            "dt_utc": dt_utc,
            "text": annotation.get("text", ""),
            "chapter": annotation.get("chapter", "") or "",
            "page": annotation.get("page"),
            "pageno": annotation.get("pageno"),
            "ventana_min": ventana_min,
            "note_inicial": annotation.get("note", ""),
        },
        "state": estado,
        "attempts": 0,
        "audios": [],
        "fortemi_note_id": None,
        "reason": reason,
        "updated": now_iso(),
    }
    reg["highlights"][key] = entry
    if entry["hl"]["dt_local"]:
        reg["by_dt_text"][_k2(book["md5"], annotation)] = key
    return entry


# ─── Reglas puras ───

def parse_dt_utc(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def fue_preexistente(dt_local, cutoff_local):
    """No-retroactividad: highlight creado antes del corte → done_empty."""
    if not dt_local:
        return False
    try:
        dt = datetime.strptime(dt_local.strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(dt_local.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return False
    return dt < cutoff_local


def en_ventana(hl_dt_utc, ts_utc, ventana_min):
    return hl_dt_utc <= ts_utc <= hl_dt_utc + timedelta(minutes=ventana_min)


def elegir_highlight(ts_utc, elegibles, ventana_default=5):
    """LIFO + elegibilidad cronológica: entre los highlights cuya ventana
    contiene ts_utc, gana el de datetime más reciente (global entre libros).
    elegibles: lista de (key, entry)."""
    mejor, mejor_dt = None, None
    for key, e in elegibles:
        dt = parse_dt_utc(e["hl"].get("dt_utc"))
        if dt is None:
            continue
        v = e["hl"].get("ventana_min") or ventana_default
        if en_ventana(dt, ts_utc, v):
            if mejor_dt is None or dt > mejor_dt:
                mejor, mejor_dt = key, dt
    return mejor


def componer_note(previa, transcripcion):
    """Borde: si había nota manual, se concatena; si no, transcripción pura."""
    if previa and previa.strip():
        return previa + "\n\n" + transcripcion
    return transcripcion


# ─── Inicialización (primer arranque: todo lo existente → done_empty) ───

def inicializar(items, cutoff_local):
    """items: [{key, md5, dt_local, text, book, ventana_min}]"""
    n = 0
    with registro() as reg:
        if reg["initialized"]:
            return 0
        for it in items:
            if it["key"] in reg["highlights"]:
                continue
            annotation = {"datetime": it["dt_local"], "text": it["text"], "note": ""}
            if ya_visto(reg, it["md5"], annotation):
                continue
            agregar(reg, it["key"], it["book"], annotation, "done_empty",
                    ventana_min=it.get("ventana_min", 5), reason="init")
            n += 1
        reg["initialized"] = True
    return n


# ─── CLI: --test ───

def _test():
    H = "2026-10-12T08:43:10Z"  # 10:43:10 Madrid = 08:43:10 UTC

    def entry(dt_utc):
        return {"hl": {"dt_utc": dt_utc, "ventana_min": 5}}

    # ventana inclusive en ambos bordes
    e1 = entry(H)
    assert elegir_highlight(parse_dt_utc("2026-10-12T08:43:10Z"), [("k1", e1)]) == "k1"
    assert elegir_highlight(parse_dt_utc("2026-10-12T08:48:10Z"), [("k1", e1)]) == "k1"
    assert elegir_highlight(parse_dt_utc("2026-10-12T08:48:11Z"), [("k1", e1)]) is None
    assert elegir_highlight(parse_dt_utc("2026-10-12T08:43:09Z"), [("k1", e1)]) is None

    # LIFO: el audio va al highlight más reciente que lo precede
    eA, eB = entry("2026-10-12T13:13:00Z"), entry("2026-10-12T13:15:00Z")
    assert elegir_highlight(parse_dt_utc("2026-10-12T13:16:00Z"),
                            [("kA", eA), ("kB", eB)]) == "kB"

    # LIFO global entre libros (md5 distintos)
    eC = entry("2026-10-12T13:17:00Z")
    assert elegir_highlight(parse_dt_utc("2026-10-12T13:18:00Z"),
                            [("kA", eA), ("kB", eB), ("kC", eC)]) == "kC"

    # varios audios: ambos califican al mismo highlight (la pasada los concatena)
    assert elegir_highlight(parse_dt_utc("2026-10-12T13:44:00Z"), [("k1", entry("2026-10-12T13:43:00Z"))]) == "k1"
    assert elegir_highlight(parse_dt_utc("2026-10-12T13:47:00Z"), [("k1", entry("2026-10-12T13:43:00Z"))]) == "k1"

    # no-retroactividad
    from datetime import datetime as _dt
    cutoff = _dt(2026, 10, 1)
    assert fue_preexistente("2026-09-30 23:59:59", cutoff) is True
    assert fue_preexistente("2026-10-01 00:00:00", cutoff) is False
    assert fue_preexistente(None, cutoff) is False

    # borde: concatenación
    assert componer_note("", "transcripción") == "transcripción"
    assert componer_note("nota manual", "transcripción") == "nota manual\n\ntranscripción"

    # identidad
    a_pdf = {"pos0": {"page": 20, "x": 78.359935332644, "y": 777.29174243279,
                      "zoom": 4.1220044226535, "rotation": 0},
             "pos1": {"page": 20, "x": 132.45982876664, "y": 778.74734494671,
                      "zoom": 4.1220044226535, "rotation": 0}}
    a_epub = {"pos0": "/body/Doc[1]/main[1]/p[10]", "pos1": "/body/Doc[1]/main[1]/p[10]"}
    k1 = key_para("abc123", a_pdf)
    k2 = key_para("abc123", a_epub)
    assert k1.startswith("abc123::") and "||" in k1
    assert k2 == "abc123::/body/Doc[1]/main[1]/p[10]||/body/Doc[1]/main[1]/p[10]"

    print("conciliacion --test: TODOS LOS ASSERTS OK")
    return 0


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        sys.exit(_test())
    print("uso: python3 conciliacion.py --test")
