#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fusionar_hipotesis.py — v2.1: SÍ hace lo que su nombre dice.

Reformatea, fusiona, renombra y renumera las hipótesis que YA están en Fortemi.
A diferencia de la v1, el juez NO decide por el resumen ni por un solo campo: lee las
hipótesis enteras (patrón, mecanismo, predicción, lo que la tumbaría, historia,
procedencia, evidencia), con criterio holístico, y permite agrupar hipótesis de
estados DISTINTOS — preservando el trazo de por qué cada una cayó o sobrevivió.

ORDEN (todo en memoria primero; escritura al final):
  1. leer_todas   — lee las notas con tag thesis de Fortemi, completas.
  2. reformatear  — convierte bloques "## Absorbida:" (formato viejo) al formato de
                    campos concatenados con timestamp de viñeta, y añade
                    "## Procedencia". Se hace EN MEMORIA antes de juzgar.
  3. juzgar       — UNA llamada al LLM con todas las hipótesis enteras. Criterio
                    holístico. Permite estatus mixtos. Imprime el response COMPLETO
                    y distingue "0 fusiones" de "fallo de parseo".
  4. fusionar     — canónica = la de menor número. Concatena los 4 campos con
                    timestamps de adherencia, unifica evidencia (dedup por note_id),
                    historia y procedencia. BORRA de verdad la absorbida.
  5. renumerar    — cierra huecos reasignando H-NNN por orden (1..N) y guarda el
                    mapa para actualizar "cited/H-NNN" en todo el corpus.
  6. titular      — corrige el H1 a "# H-{num} — {asunto}", alineando título con el
                    tag final. Si la nota no tiene asunto (las más antiguas), lo genera
                    con el LLM (una llamada).
  7. escribir     — purge→DELETE, PATCH de contenido, PUT de tags. Sin cascada.

USO:
    n3run python /app/fusionar_hipotesis.py --dry-run
    n3run python /app/fusionar_hipotesis.py
    n3run python /app/fusionar_hipotesis.py --sin-fusionar   # solo reformatear+renumerar+titular
"""

from __future__ import annotations
import argparse, importlib.util, json, re, sys, unicodedata
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
H_RE = re.compile(r"[Hh]-(\d{3,})")
# Bug: H_RE.fullmatch no matchea "cited/h-001" (tiene prefijo "cited/").
# Fix: CID_RE con .match() — busca el patron al principio del string.
CID_RE = re.compile(r"^cited/[Hh]-(\d{3,})$")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# ESCRITURA
# ══════════════════════════════════════════════════════════════════════════════

def _sql_content(note_id: str, content: str) -> bool:
    """Actualiza note_original.content via psycopg2, sin disparar cascada.

    PATCH /notes/{id} con content dispara el pipeline NLP completo. La unica
    via sin cascada es SQL directo. [Verificado en main.rs]

    Credenciales del docker-compose.yml (visibles dentro del stack):
    workstation-postgres:5432, user=matric, db=matric.
    """
    try:
        import os, psycopg2
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "workstation-postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "matric"),
            password=os.environ.get("POSTGRES_PASSWORD", "fortemi-local-dev"),
            dbname=os.environ.get("POSTGRES_DB", "matric"))
        conn.autocommit = False
        from datetime import datetime, timezone as tz
        ts = datetime.now(tz.utc)
        with conn.cursor() as cur:
            cur.execute("UPDATE note_original SET content = %s, user_last_edited_at = %s "
                        "WHERE note_id = %s::uuid", (content, ts, note_id))
            cur.execute("UPDATE note SET updated_at_utc = %s WHERE id = %s::uuid",
                        (ts, note_id))
        conn.commit(); conn.close()
        return True
    except ImportError:
        return False
    except Exception as e:
        print(f"  AVISO SQL content {note_id}: {e}")
        return False


def _sql_title(note_id: str, title: str) -> bool:
    """Actualiza note.title via PATCH title-only.

    PATCH con solo title (sin content) NO dispara cascada. Verificado en
    Fortemi main.rs: content_changed = body.content.is_some(); la cascada
    solo se encola si content_changed es true. [linea 13496]
    """
    try:
        import os, psycopg2
        conn = psycopg2.connect(
            host=os.environ.get("POSTGRES_HOST", "workstation-postgres"),
            port=int(os.environ.get("POSTGRES_PORT", "5432")),
            user=os.environ.get("POSTGRES_USER", "matric"),
            password=os.environ.get("POSTGRES_PASSWORD", "fortemi-local-dev"),
            dbname=os.environ.get("POSTGRES_DB", "matric"))
        from datetime import datetime, timezone as tz
        ts = datetime.now(tz.utc)
        with conn.cursor() as cur:
            cur.execute("UPDATE note SET title = %s, updated_at_utc = %s WHERE id = %s::uuid",
                        (title, ts, note_id))
        conn.commit(); conn.close()
        return True
    except ImportError:
        return False
    except Exception as e:
        print(f"  AVISO SQL title {note_id}: {e}")
        return False

def cargar_parser():
    spec = importlib.util.spec_from_file_location("ep", AQUI / "e2_parser.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cargar_concatenacion():
    spec = importlib.util.spec_from_file_location("cc", AQUI / "_concatenacion.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════
# PARSEO DEL CUERPO
# ══════════════════════════════════════════════════════════════════════════════

def seccion_flex(cuerpo: str, nombre: str) -> str:
    """Como cc.seccion() pero tolera paréntesis en el título:
    '## Evidencia (5 notas)' también matchea con nombre='Evidencia'."""
    m = re.search(
        rf"^##\s*{re.escape(nombre)}[^\n]*\n(.*?)(?=\n##\s|\n---\s*\n|\Z)",
        cuerpo, re.S | re.M)
    return m.group(1).strip() if m else ""


def seccion_cc(cuerpo: str, nombre: str, cc) -> str:
    """Campos simples ## Patron / ## Mecanismo / ## Prediccion / ## Falsador."""
    s = cc.seccion(cuerpo, nombre)
    return s if s else seccion_flex(cuerpo, nombre)


def parse_absorbidas(cuerpo: str) -> list[dict]:
    """Extrae los bloques '## Absorbida: H-0XX (venia con estado activo)' del
    formato viejo, con sus 4 campos en **Negrita:**."""
    absorbidas = []
    patron = re.compile(
        r"^##\s*Absorbida:\s*(\S+)\s*\(([^)]+)\)\s*\n(.*?)(?=\n##\s|\n---\s*\n|\Z)",
        re.S | re.M)
    for m in patron.finditer(cuerpo):
        hid = m.group(1).strip()
        info = m.group(2).strip()
        crudo = m.group(3).strip()
        campos = {}
        for campo in ("Patron", "Mecanismo", "Prediccion", "Falsador"):
            pm = re.search(rf"\*\*{campo}:\*\*\s*(.*?)(?=\n\*\*|\Z)", crudo, re.S)
            if pm:
                campos[campo.lower()] = pm.group(1).strip()
        estado = "falsado" if "falsado" in info.lower() else "activo"
        absorbidas.append({"id_local": hid, "estado": estado, "info": info, **campos})
    return absorbidas


def extraer_propuestas(historia: str) -> list[str]:
    """Timestamps de los eventos 'proposed', en orden cronológico."""
    out = []
    for m in re.finditer(r"`([^`]+)`\s+\*\*proposed\*\*", historia):
        ts = m.group(1).strip()
        out.append(ts[:16].replace("T", " ") if "T" in ts else ts[:16])
    return out


def eventos(historia_texto: str) -> list[str]:
    """Las líneas '- `ts` **evento** ...' del bloque Historia."""
    lineas = []
    for ln in (historia_texto or "").split("\n"):
        ln = ln.rstrip()
        if re.match(r"^-\s*`", ln):
            lineas.append(ln)
    return lineas


def entradas_evidencia(sec: str) -> list[tuple[str, str]]:
    """[(note_id, resto_de_la_entrada)] de un bloque Evidencia/Contra-evidencia."""
    out: list[tuple[str, str]] = []
    cur_id, cur_txt = None, []
    def flush():
        nonlocal cur_id, cur_txt
        if cur_id:
            out.append((cur_id, "\n".join(cur_txt).strip()))
        cur_id, cur_txt = None, []
    for ln in sec.split("\n"):
        m = re.match(r"^\s*-\s*`([^`]+)`(.*)$", ln)
        if m and UUID_RE.fullmatch(m.group(1).strip()):
            flush()
            cur_id = m.group(1).strip()
            cur_txt = [m.group(2).strip()]
        elif cur_id:
            cur_txt.append(ln.rstrip())
    flush()
    return out


def armar_evidencia(entradas: list[tuple[str, str]], titulo: str = "Evidencia") -> str:
    """Reconstruye '## {titulo} (N notas)' + entradas, dedup por note_id."""
    unicos: dict[str, str] = {}
    for nid, txt in entradas:
        if nid and nid not in unicos:
            unicos[nid] = txt
    lineas = [f"## {titulo} ({len(unicos)} notas)"]
    for nid, txt in unicos.items():
        lineas.append(f"- `{nid}`{txt}")
    return "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════════════
# REFORMATEO: bloques "## Absorbida:" → campos concatenados + Procedencia
# ══════════════════════════════════════════════════════════════════════════════

def reformatear_nota(cuerpo: str, cc, ts_creacion: str) -> tuple[str, list[dict]]:
    """Convierte los bloques ## Absorbida: del formato viejo al formato de
    concatenación con timestamp de viñeta, y genera la lista de procedencia.

    Devuelve (nuevo_cuerpo, procedencia). Si no hay bloques, devuelve el cuerpo
    sin tocar y []. Suposición documentada: los bloques están en orden de absorción
    (= orden de registro) y las propuestas de la historia (menos la de la canónica)
    están en el mismo orden cronológico.
    """
    absorbidas = parse_absorbidas(cuerpo)
    if not absorbidas:
        return cuerpo, []

    historia_sec = seccion_flex(cuerpo, "Historia")
    propuestas = extraer_propuestas(historia_sec)
    ts_canon = ts_creacion[:16].replace("T", " ") if ts_creacion else cc.ahora()
    ts_abs = (propuestas[1:1 + len(absorbidas)] if len(propuestas) > 1 else [])
    while len(ts_abs) < len(absorbidas):
        ts_abs.append(ts_canon)

    pattern = seccion_cc(cuerpo, "Patron", cc)
    mechanism = seccion_cc(cuerpo, "Mecanismo", cc)
    prediction = seccion_cc(cuerpo, "Prediccion", cc)
    falsifier = seccion_cc(cuerpo, "Falsador", cc)

    concedidas = []
    for m in re.finditer(r"`([^`]+)`\s+\*\*conceded\*\*[^—]*—\s*(.+?)(?=\n-|\Z)",
                         historia_sec, re.S):
        ts = m.group(1).strip()
        ts = ts[:16].replace("T", " ") if "T" in ts else ts[:16]
        concedidas.append((ts, m.group(2).strip()))

    falsadas = [a for a in absorbidas if a["estado"] == "falsado"]
    concedidas_match = concedidas[:len(falsadas)] if len(concedidas) >= len(falsadas) else concedidas
    fi = 0
    procedencia: list[dict] = []

    for i, a in enumerate(absorbidas):
        ts_a = ts_abs[i]
        if a.get("patron"):
            pattern = cc.fundir_campo(pattern, ts_canon, a["patron"], ts_a)
        if a.get("mecanismo"):
            mechanism = cc.fundir_campo(mechanism, ts_canon, a["mecanismo"], ts_a)
        if a.get("prediccion"):
            prediction = cc.fundir_campo(prediction, ts_canon, a["prediccion"], ts_a)
        if a.get("falsador"):
            falsifier = cc.fundir_campo(falsifier, ts_canon, a["falsador"], ts_a)

        if a["estado"] == "falsado" and fi < len(concedidas_match):
            ts_c, reason = concedidas_match[fi]
            motivo = f"concedida: {reason[:180]}"
            procedencia.append({"timestamp": ts_c, "estado": "falsada",
                                "motivo": motivo, "id_local": a["id_local"]})
            fi += 1
        else:
            procedencia.append({"timestamp": ts_a, "estado": "activa",
                                "motivo": "absorbida por duplicate_of",
                                "id_local": a["id_local"]})

    nuevo = cuerpo
    nuevo = cc.reemplazar_seccion(nuevo, "Patron", pattern)
    nuevo = cc.reemplazar_seccion(nuevo, "Mecanismo", mechanism)
    nuevo = cc.reemplazar_seccion(nuevo, "Prediccion", prediction)
    nuevo = cc.reemplazar_seccion(nuevo, "Falsador", falsifier)
    nuevo = re.sub(
        r"^##\s*Absorbida:\s*\S+\s*\([^)]*\)\s*\n.*?(?=\n##\s|\n---\s*\n|\Z)",
        "", nuevo, flags=re.S | re.M)
    nuevo = re.sub(r"\n{3,}", "\n\n", nuevo)
    return nuevo, procedencia


def agregar_procedencia(cuerpo: str, procedencia: list[dict]) -> str:
    if not procedencia:
        return cuerpo
    lineas = ["## Procedencia", ""]
    for p in procedencia:
        lineas.append(f"- `{p['timestamp']}` — hipótesis local del debate, "
                      f"{p['estado']} — {p['motivo']}")
    lineas.append("")
    sep = cuerpo.rfind("\n---\n")
    if sep >= 0:
        return cuerpo[:sep] + "\n" + "\n".join(lineas) + cuerpo[sep:]
    return cuerpo.rstrip() + "\n\n" + "\n".join(lineas)


# ══════════════════════════════════════════════════════════════════════════════
# LECTURA
# ══════════════════════════════════════════════════════════════════════════════

def leer_todas(ep, cc) -> list[dict]:
    """Todas las notas con tag thesis, leídas completas y reformateadas en memoria."""
    fuera: list[dict] = []
    for n in ep.notas_por_tag("thesis", 5000):
        nid = n.get("id")
        nota = ep.get_note(nid)
        if not nota:
            continue
        cuerpo = nota.get("content") or ""
        tags = []
        for t in (nota.get("tags") or []):
            tags.append(t.get("name", t) if isinstance(t, dict) else t)
        num = None
        estado = ""
        periodos = []
        for t in tags:
            m = H_RE.fullmatch(str(t).strip())
            if m:
                num = int(m.group(1))
            elif str(t).lower().startswith("estado/"):
                estado = str(t).split("/", 1)[1]
            elif str(t).lower().startswith("periodo/"):
                periodos.append(str(t).split("/", 1)[1])
        if num is None:
            continue

        creada = (nota.get("created_at_utc") or "")[:16].replace("T", " ")
        cuerpo_ref, procedencia = reformatear_nota(cuerpo, cc, creada)
        cuerpo_ref = agregar_procedencia(cuerpo_ref, procedencia)

        mh1 = re.search(r"^#\s*[Hh]-\d+\s*[—-]\s*(.+)$", cuerpo, re.M)
        asunto = mh1.group(1).strip() if mh1 else ""
        mcita = re.search(r"^>\s*(.+)$", cuerpo, re.M)
        resumen = mcita.group(1).strip() if mcita else ""

        fuera.append({
            "note_id": nid, "num": num, "tag": f"H-{num:03d}",
            "estado": estado or "activo",
            "periodos": sorted(periodos),
            "asunto": asunto, "resumen": resumen[:240],
            "pattern": seccion_cc(cuerpo_ref, "Patron", cc),
            "mechanism": seccion_cc(cuerpo_ref, "Mecanismo", cc),
            "prediction": seccion_cc(cuerpo_ref, "Prediccion", cc),
            "falsifier": seccion_cc(cuerpo_ref, "Falsador", cc),
            "historia": seccion_flex(cuerpo_ref, "Historia"),
            "evidencia": seccion_flex(cuerpo_ref, "Evidencia"),
            "contra": seccion_flex(cuerpo_ref, "Contra-evidencia"),
            "procedencia": procedencia,
            "creada": creada,
            "cuerpo": cuerpo_ref, "cuerpo_original": cuerpo,
            "collection_id": nota.get("collection_id"),
            "tags": tags, "_borrada": False,
        })
    fuera.sort(key=lambda x: x["num"])
    return fuera


# ══════════════════════════════════════════════════════════════════════════════
# JUICIO — criterio holístico, estatus mixtos permitidos, log COMPLETO
# ══════════════════════════════════════════════════════════════════════════════

def _json_robusto(raw: str):
    """Limpia fences, extrae el JSON y prueba reparaciones. NO asume `[{` adyacente,
    NO falla por un `}` truncado ni por comas colgantes."""
    raw = re.sub(r"```(?:json)?|```", "", (raw or "").strip()).strip()
    inicio = [i for i in (raw.find("["), raw.find("{")) if i >= 0]
    if not inicio:
        return None
    ini = min(inicio)
    fin = max(raw.rfind("]"), raw.rfind("}"))
    if fin < ini:
        return None
    chunk = raw[ini:fin + 1]
    candidatos = [chunk, re.sub(r",\s*([}\]])", r"\1", chunk)]
    for c in candidatos:
        try:
            return json.loads(c)
        except Exception:
            continue
    return None


def juzgar(ep, hs: list[dict]) -> tuple[list[dict], dict]:
    """Una llamada con todas las hipótesis enteras. Devuelve (grupos, informe)."""
    if len(hs) < 2:
        return [], {"estado": "menos_de_2"}
    sistema = (
        "Te doy TODAS las hipótesis que un análisis ha ido produciendo sobre un corpus "
        "personal, de varios períodos distintos. Agrupa las que son en realidad el MISMO "
        "HALLAZGO dicho de maneras distintas.\n\n"
        "LEE CADA UNA ENTERA. Cada entrada trae sus campos completos —patrón, mecanismo, "
        "predicción, lo que la tumbaría, historia, procedencia y evidencia—. NO decidas "
        "por un solo campo: dos hipótesis pueden hablar de lo mismo con mecánicas, "
        "predicciones o falsadores distintos, porque son el mismo fenómeno visto desde "
        "ángulos distintos. Lo que importa es si, leídas enteras, describen el mismo "
        "hallazgo.\n\n"
        "PUEDES AGRUPAR hipótesis de estados DISTINTOS (activa, falsada, sin_responder). "
        "Que una cayera y otra sobreviviera no las hace hallazgos distintos; el trazo de "
        "por qué cada una cayó o se sostuvo se conserva completo en la canónica.\n\n"
        "SESGO A NO FUSIONAR. Dejar dos parecidas separadas se ve y se arregla después; "
        "fusionar donde no corresponde mezcla las voces sin remedio. Ante la duda, NO "
        "fusiones.\n\n"
        "Para cada grupo que SÍ fusiones, da:\n"
        "  asunto  — de qué va el hallazgo unido, 2 a 6 palabras, en español\n"
        "  resumen — el meollo en UNA línea, como se lo contarías a alguien con prisa\n\n"
        "Responde SOLO un array JSON: "
        '[{"ids": ["H-001","H-014"], "asunto": "...", "resumen": "..."}]\n'
        "Sólo grupos de dos o más. Si ninguna se fusiona, responde [].")
    partes = []
    for h in hs:
        ev = " ".join(m.group(1) for m in re.finditer(r"`([^`]+)`", h["evidencia"]))
        partes.append(
            f"{h['tag']}  [estado: {h['estado']}]  [periodos: {', '.join(h['periodos']) or '?'}]\n"
            + (f"  asunto:   {h['asunto']}\n" if h["asunto"] else "  asunto:   (sin título)\n")
            + (f"  resumen:  {h['resumen']}\n" if h["resumen"] else "")
            + f"  PATRÓN:    {h['pattern']}\n"
            + f"  MECANISMO:  {h['mechanism']}\n"
            + f"  PREDICCIÓN: {h['prediction']}\n"
            + f"  LO QUE LA TUMBARÍA: {h['falsifier']}\n"
            + (f"  HISTORIA:\n{h['historia']}\n" if h["historia"] else "")
            + (f"  PROCEDENCIA:\n"
               + "\n".join(f"  - {p['timestamp']} — {p['estado']} — {p['motivo']}"
                           for p in h["procedencia"]) + "\n"
               if h["procedencia"] else "  PROCEDENCIA: (ninguna)\n")
            + f"  EVIDENCIA (note_ids): {ev or '(vacía)'}")
    try:
        raw = ep._llm(sistema, "\n\n".join(partes), esfuerzo="medium")
    except Exception as e:
        print(f"  ERROR en _llm juzgando: {e}")
        return [], {"estado": "error_llm", "error": str(e)}
    print(f"  [debug] response crudo del juez ({len(raw)} chars):\n{raw}")
    data = _json_robusto(raw)
    if data is None:
        print("  AVISO: no se pudo extraer JSON de la respuesta del juez "
              "(respuesta completa arriba).")
        return [], {"estado": "parse_fallido"}
    if not isinstance(data, list):
        print(f"  AVISO: el juez devolvió {type(data).__name__}, no una lista.")
        return [], {"estado": "no_es_lista"}
    grupos = []
    for g in data:
        ids = [str(x).strip().upper() for x in (g.get("ids") or [])]
        miembros = [h for h in hs if h["tag"] in ids]
        if len(miembros) >= 2:
            grupos.append({"ids": ids, "miembros": miembros,
                          "asunto": (g.get("asunto") or "")[:80],
                          "resumen": (g.get("resumen") or "")[:240]})
    print(f"  → {len(grupos)} grupo(s) de fusión")
    return grupos, {"estado": "ok"}


# ══════════════════════════════════════════════════════════════════════════════
# EJECUCIÓN DE FUSIONES (mecanismo 2: hipótesis que YA tienen nota separada)
# ══════════════════════════════════════════════════════════════════════════════

def _ts_adherencia(h: dict) -> str:
    for m in re.finditer(r"`([^`]+)`\s+\*\*proposed\*\*", h["historia"]):
        ts = m.group(1).strip()
        return ts[:16].replace("T", " ") if "T" in ts else ts[:16]
    return h["creada"] or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def fusionar_grupo(cc, canon: dict, resto: list[dict], informe: dict) -> list[int]:
    """Concatena campos de 'resto' en 'canon', unifica evidencia/historia/
    procedencia. Marca 'resto' como _borrada. Devuelve los números borrados."""
    borrados = []
    if canon["_borrada"]:
        raise RuntimeError(f"{canon['tag']} ya fue borrada; revisa el grupo.")
    c_ts = _ts_adherencia(canon)
    for otro in resto:
        if otro["_borrada"]:
            continue
        o_ts = _ts_adherencia(otro)
        for campo, seccion in (("pattern", "Patron"), ("mechanism", "Mecanismo"),
                               ("prediction", "Prediccion"), ("falsifier", "Falsador")):
            if otro[campo]:
                canon[campo] = cc.fundir_campo(canon[campo], c_ts, otro[campo], o_ts)
        canon["evidencia"] = armar_evidencia(
            entradas_evidencia(canon["evidencia"]) + entradas_evidencia(otro["evidencia"]))
        canon["contra"] = armar_evidencia(
            entradas_evidencia(canon["contra"]) + entradas_evidencia(otro["contra"]),
            titulo="Contra-evidencia")
        evs = eventos(canon["historia"]) + eventos(otro["historia"])
        canon["historia"] = "\n".join(dict.fromkeys(evs))
        canon["procedencia"].extend(otro["procedencia"])
        otro["_borrada"] = True
        borrados.append(otro["num"])
        informe["fusiones"].append({"canónica": canon["tag"], "absorbe": otro["tag"],
                                    "estado_absorbida": otro["estado"]})
    cuerpo = canon["cuerpo"]
    cuerpo = cc.reemplazar_seccion(cuerpo, "Patron", canon["pattern"])
    cuerpo = cc.reemplazar_seccion(cuerpo, "Mecanismo", canon["mechanism"])
    cuerpo = cc.reemplazar_seccion(cuerpo, "Prediccion", canon["prediction"])
    cuerpo = cc.reemplazar_seccion(cuerpo, "Falsador", canon["falsifier"])
    if canon["evidencia"]:
        cuerpo = cc.reemplazar_seccion(cuerpo, "Evidencia", canon["evidencia"])
    if canon["contra"]:
        cuerpo = cc.reemplazar_seccion(cuerpo, "Contra-evidencia", canon["contra"])
    if canon["historia"]:
        cuerpo = cc.reemplazar_seccion(cuerpo, "Historia", canon["historia"])
    cuerpo = agregar_procedencia(cuerpo, canon["procedencia"])
    canon["cuerpo"] = cuerpo
    return borrados


# ══════════════════════════════════════════════════════════════════════════════
# RENUMERACIÓN Y RECITADO
# ══════════════════════════════════════════════════════════════════════════════

def renumerar_y_recitar(ep, sobrevivientes: list[dict], borrados: set[int],
                       informe: dict) -> dict[int, int]:
    """Cierra los huecos: mapea número_viejo→nuevo para los sobrevivientes que
    quedaron por delante de un hueco. Devuelve el mapa (no escribe)."""
    mapa: dict[int, int] = {}
    if not borrados:
        return mapa
    vivas = sorted(h["num"] for h in sobrevivientes if not h["_borrada"])
    for i, viejo in enumerate(vivas):
        nuevo = i + 1
        if viejo != nuevo:
            mapa[viejo] = nuevo
    if mapa:
        print("  renumerando:" + "  ".join(f"H-{v:03d}->H-{n:03d}" for v, n in sorted(mapa.items())))
        informe["renumeradas"] = [{"de": f"H-{v:03d}", "a": f"H-{n:03d}"}
                                  for v, n in sorted(mapa.items())]
    return mapa


def recitar(ep, mapa: dict[int, int], informe: dict, dry: bool) -> int:
    """Recorre el corpus: cada nota que tenga un tag cited/h-0XX cuyo número cambió,
    lo reemplaza por el nuevo. Devuelve cuántos tags se actualizaron."""
    if not mapa:
        return 0
    actualizados = 0
    viejos = {f"cited/h-{v:03d}" for v in mapa}
    for tag_viejo in sorted(viejos):
        try:
            citantes = ep.notas_por_tag(tag_viejo, 2000)
        except Exception as e:
            informe["errores"].append(f"{tag_viejo}: {e}")
            continue
        for n in citantes:
            nid = n.get("id")
            actuales = ep.leer_tags(nid)
            if actuales is None:
                informe["errores"].append(f"no se pudieron leer tags de {nid}")
                continue
            nuevos = [ep.sanear_tag(t) for t in actuales]
            cambio = False
            for t in list(nuevos):
                # Fix: CID_RE detecta "cited/h-001"; H_RE.fullmatch no.
                m = CID_RE.match(str(t).strip())
                if m and int(m.group(1)) in mapa:
                    nv = mapa[int(m.group(1))]
                    nuevos.remove(t)
                    nuevos.append(f"cited/h-{nv:03d}")
                    cambio = True
            if not cambio:
                continue
            if dry:
                print(f"  [dry] recitar {nid}: {len(actuales)} -> {len(set(nuevos))} tags")
            else:
                ep.api("PUT", f"/api/v1/notes/{nid}/tags",
                       json={"tags": sorted(set(nuevos))}, union_verificada=True)
            actualizados += 1
    informe["cited_actualizados"] = actualizados
    return actualizados


def titular(ep, hs: list[dict], mapa: dict[int, int], informe: dict, dry: bool) -> None:
    """Garantiza H1 = '# H-{num} — {asunto}' para todo sobreviviente. Genera asuntos
    (LLM, una llamada) para los que el H1 no trae. Aplica el número final (mapa)."""
    vivas = [h for h in hs if not h["_borrada"]]
    faltan = [h for h in vivas if not h["asunto"]]
    if faltan:
        sistema = (
            "Para cada hipótesis escribe su ASUNTO: de qué va, de 2 a 6 palabras, en "
            "español, minúsculas, sin comillas ni puntuación. Es el título que un humano "
            "va a ver.\nResponde SOLO un objeto JSON {\"H-001\": \"asunto\", ...}. "
            "Todas tienen que aparecer.")
        usuario = "\n\n".join(f"{h['tag']}\n  {h['pattern'][:400]}" for h in faltan)
        try:
            data = _json_robusto(ep._llm(sistema, usuario, esfuerzo="low")) or {}
        except Exception as e:
            informe["errores"].append(f"asuntos: {e}")
            data = {}
        for h in faltan:
            a = " ".join(str(data.get(h["tag"], "")).strip().strip('"\'_').split())[:80]
            if a and len(a) >= 3:
                h["asunto"] = a
                informe["asuntos_generados"] += 1
                print(f"  asunto {h['tag']}: {a}")
    for h in vivas:
        num_final = mapa.get(h["num"], h["num"])
        titulo = f"# H-{num_final:03d}"
        if h["asunto"]:
            titulo += f" — {h['asunto']}"
        h["cuerpo"] = re.sub(r"^#.*$", titulo, h["cuerpo"], count=1, flags=re.M)


# ══════════════════════════════════════════════════════════════════════════════
# ESCRITURA
# ══════════════════════════════════════════════════════════════════════════════



def escribir(ep, hs: list[dict], mapa: dict[int, int], informe: dict, dry: bool) -> None:
    borradas = [h for h in hs if h["_borrada"]]
    vivas = [h for h in hs if not h["_borrada"]]
    # 1. Borrar de verdad (con fallback a soft delete)
    for h in borradas:
        if dry:
            print(f"  [dry] BORRAR {h['tag']} (nota {h['note_id']})")
            continue
        try:
            ep.api("POST", f"/api/v1/notes/{h['note_id']}/purge")
            print(f"  BORRADA {h['tag']} (nota {h['note_id']})")
        except Exception as e:
            try:
                ep.api("DELETE", f"/api/v1/notes/{h['note_id']}")
                print(f"  BORRADA (soft) {h['tag']} — {type(e).__name__}")
            except Exception as e2:
                informe["errores"].append(f"no se pudo borrar {h['tag']}: {e2}")
    # 2. Actualizar contenido de las notas que cambiaron.
    #
    # [OBSERVADO] PATCH /notes/{id} con content dispara el pipeline NLP
    # completo (ai_revision, title_generation, concept_tagging, ...). No hay
    # forma de acotarlo en PATCH: UpdateNoteBody no tiene campo pipeline, y el
    # handler lo pasa siempre como None. [Verificado en main.rs lineas 13528-13542]
    #
    # Fix: SQL directo a note_original (no encola ningun job). Fallback a PATCH
    # si el SQL no esta disponible (p.ej. fuera del stack Docker).
    for h in vivas:
        if h["cuerpo"] == h["cuerpo_original"]:
            continue
        if dry:
            print(f"  [dry] PATCH {h['tag']} (contenido)")
            continue
        try:
            _ok = _sql_content(h["note_id"], h["cuerpo"])
            if _ok:
                print(f"  SQL content {h['tag']}")
            else:
                # Fallback a PATCH si Docker no disponible
                ep.api("PATCH", f"/api/v1/notes/{h['note_id']}", json={"content": h["cuerpo"]})
                print(f"  PATCH {h['tag']}: contenido (SQL no disponible)")
        except Exception as e:
            informe["errores"].append(f"content {h['tag']}: {e}")
    # 3. PUT de tags para las renumeradas (título ya quedó con el número final)
    for h in vivas:
        num_final = mapa.get(h["num"], h["num"])
        if num_final == h["num"]:
            continue
        actuales = ep.leer_tags(h["note_id"])
        if actuales is None:
            informe["errores"].append(f"no se pudieron leer tags de {h['note_id']}")
            continue
        limpios = {ep.sanear_tag(t) for t in actuales
                   if not H_RE.fullmatch(str(t).split("/")[-1].strip())}
        limpios.add(f"h-{num_final:03d}")
        if dry:
            print(f"  [dry] PUT tags {h['tag']} -> h-{num_final:03d}")
            continue
        ep.api("PUT", f"/api/v1/notes/{h['note_id']}/tags",
               json={"tags": sorted(limpios)}, union_verificada=True)
        print(f"  PUT tags {h['tag']} -> h-{num_final:03d}")
    # 4. Recitar cited
    recitar(ep, mapa, informe, dry)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repara, reformatea, fusiona y renumera hipótesis de Fortemi")
    ap.add_argument("--dry-run", action="store_true", help="No escribe nada")
    ap.add_argument("--sin-fusionar", action="store_true",
                   help="No juzgar ni fusionar; solo reformatear + renumerar + titular")
    ap.add_argument("--sin-renumerar", action="store_true")
    ap.add_argument("--sin-titulos", action="store_true",
                   help="No corregir H1 ni generar asuntos")
    args = ap.parse_args()

    ep = cargar_parser()
    cc = cargar_concatenacion()
    informe = {"cuando": datetime.now(timezone.utc).isoformat(),
               "dry_run": args.dry_run, "leidas": 0, "reformateadas": 0,
               "fusiones": [], "borradas": [], "renumeradas": [],
               "cited_actualizados": 0, "asuntos_generados": 0, "errores": []}

    hs = leer_todas(ep, cc)
    informe["leidas"] = len(hs)
    print(f"{len(hs)} notas con tag thesis en Fortemi "
          "(varias contienen hipótesis absorbidas en su interior)")
    for h in hs:
        print(f"  {h['tag']} [{h['estado']:13}] "
              f"[{', '.join(h['periodos']) or '?':10}] {h['asunto'][:52] or '(sin título)'}")

    informe["reformateadas"] = sum(1 for h in hs if h["procedencia"])
    print(f"\n{informe['reformateadas']} nota(s) con bloques ## Absorbida: a reformatear")

    if not args.sin_fusionar and len(hs) >= 2:
        print("\njuzgando cuáles son el mismo hallazgo (criterio holístico)...")
        grupos, _ = juzgar(ep, hs)
        for grupo in grupos:
            miembros = sorted(grupo["miembros"], key=lambda x: x["num"])
            canon, resto = miembros[0], miembros[1:]
            print(f"  {canon['tag']} <- {' + '.join(m['tag'] for m in resto)}"
                  + (f"   \"{grupo['asunto']}\"" if grupo["asunto"] else ""))
            try:
                borrados = fusionar_grupo(cc, canon, resto, informe)
                informe["borradas"].extend(f"H-{b:03d}" for b in borrados)
                if grupo["asunto"] and not args.sin_titulos:
                    canon["asunto"] = grupo["asunto"][:80]
            except Exception as e:
                informe["errores"].append(f"fusionar {canon['tag']}: {e}")

    borrados = {int(x[2:]) for x in informe["borradas"]}
    mapa = {}
    if not args.sin_renumerar:
        print()
        mapa = renumerar_y_recitar(ep, hs, borrados, informe)

    if not args.sin_titulos:
        print()
        titular(ep, hs, mapa, informe, args.dry_run)

    print()
    escribir(ep, hs, mapa, informe, args.dry_run)

    # ── Resumen ──
    print()
    print(f"leídas={informe['leidas']} reformateadas={informe['reformateadas']} "
          f"fusiones={len(informe['fusiones'])} borradas={len(informe['borradas'])} "
          f"renumeradas={len(informe['renumeradas'])} "
          f"cited={informe['cited_actualizados']} asuntos={informe['asuntos_generados']}")
    for f in informe["fusiones"]:
        print(f"  {f['canónica']} absorbió {f['absorbe']} "
              f"(estado previo: {f['estado_absorbida']})")
    if informe["errores"]:
        print(f"{len(informe['errores'])} error(es):")
        for e in informe["errores"][:8]:
            print(f"  · {e}")
    try:
        salida = AQUI / "fusion_hipotesis.json"
        salida.write_text(json.dumps(informe, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"informe -> {salida}")
    except Exception as e:
        print(f"no se pudo escribir informe: {e}")
    if args.dry_run:
        print("NADA escrito. Repite sin --dry-run cuando lo veas bien.")
    return 1 if informe["errores"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrumpido", flush=True)
        sys.exit(130)