#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Black Boxer — daemon de cajas negras cuasi-lossless para conversaciones IA.

Siempre arriba. Dos puertas, una cola serial (una caja a la vez: cada caja
cuesta DeepSeek y el gasto se mantiene visible):

  carpeta   /cajas/inbox/  conversaciones nuevas. Acepta dos formatos:
              .md    — export nativo de Tinfoil: ya ES el formato que recibe
                       la LLM; no se parsea.
              .json  — conversations.json (varias conversaciones): cada una
                       se parsea a .md, y ese .md es lo que entra a Fortemi
                       (note_original) y lo que recibe la LLM.
            El archivo se mueve a procesados/ cuando rindió entero.
  HTTP      POST /cajas {note_ids}   notas ya en Fortemi sin caja (vía querier).
            GET  /estado?ids=…        progreso para la barra del cliente.
            GET  /salud               latido.

La caja vive en note_revised_current — lo que la búsqueda lee (tsv generado:
buscable por FTS de gratis). La cruda vive intacta en note_original.
Detección por marcador: el mismo string que busca el querier, importado de
querier.py — UNA sola fuente hasta que el protocolo 7.5 lo oficialice.

Recuperación gratis: si el daemon muere a media caja, el archivo sigue en
inbox y se reprocesa al reiniciar; lo ya hecho se omite — y si una nota quedó
creada sin caja, se detecta y se le genera la caja (no se duplica la nota).

Reglas de la casa:
  - content y título por SQL directo. Cero PATCH de content (cascada siempre).
    El UPDATE al revised no dispara nada (sonda 30-sep: cero triggers).
  - Fail-closed: la caja que no valida (marcador inicial + === FIN ===) no se
    escribe; en la vía import la nota se crea SOLO si la caja validó.
  - Cuasi-lossless sin tope de longitud: los detalles técnicos no se omiten.
"""

from __future__ import annotations
import hashlib, json, os, queue, shutil, threading, time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg2
import requests

# El marcador y las fuentes viven en querier.py — una sola fuente de verdad.
from querier import MARCA_CAJA, FUENTES_CONV

# ─── Config ───
REST = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000").rstrip("/")
API = f"{REST}/api/v1"
LLM_URL = os.environ.get("TINFOIL_BASE_URL", "http://tinfoil-proxy:3302/v1").rstrip("/")
LLM_KEY = os.environ.get("TINFOIL_API_KEY", "")
MODELO = os.environ.get("BLACK_BOXER_MODEL", "deepseek-v4-1-flash")   # alias Tinfoil [U]
TIMEOUT_LLM = 1800            # 30 min: una caja cuasi-lossless larga puede tardar
MAX_TOKENS_SALIDA = 32768     # si el enclave rechaza (400), bajar a 16384
INTENTOS_LLM = 2              # sin tools ni rechazos consecutivos: el hang conocido no aplica
REASONING_EFFORT = os.environ.get("BLACK_BOXER_EFFORT", "high")
# temperature FUERA: en modo thinking se acepta y se ignora (no-op verificado).
# El ajuste va en la prompt. reasoning_effort explícito aunque "high" sea el
# default — documenta la intención; el proxy lo pasa (fix CN36).
PUERTO = int(os.environ.get("BLACK_BOXER_PUERTO", "3304"))
POLL_LARGO, POLL_CORTO = 10, 2

CAJAS = Path(os.environ.get("CAJAS_DIR", "/cajas"))
INBOX = CAJAS / "inbox"
PROCESADOS = CAJAS / "procesados"
FALLADOS = CAJAS / "fallados"
ESTADO_DIR = CAJAS / "estado"
LOG_FILE = ESTADO_DIR / "black_boxer.log"
LOG_MAX = 5_000_000

PG = dict(
    host=os.environ.get("POSTGRES_HOST", "postgres"),
    port=os.environ.get("POSTGRES_PORT", "5432"),
    dbname="matric", user="matric",
    password=os.environ.get("POSTGRES_PASSWORD", ""),
)

PROMPT_CAJA = """<rol>
Eres un archivista. Tu oficio: convertir una conversación larga en un REGISTRO \
DE RECUPERACIÓN — el documento que la reemplaza para cualquier lector futuro.
</rol>

<tarea>
Recibirás UNA conversación. Puede venir precedida de un encabezado con su \
título y su fecha. El cuerpo de la conversación está en Markdown: cada turno \
empieza con un encabezado "## User" (el humano) o "## Assistant" (la IA), y \
los turnos se separan con una línea "---". Todo lo que aparece bajo un \
encabezado es contenido de ese turno — incluidos, si los hay, razonamiento \
interno de la IA y adjuntos pegados. Produce su registro de recuperación.
</tarea>

<que_es>
Nadie va a abrir la conversación entera: el lector futuro — humano o IA que \
necesite saber qué pasó aquí para retomar un trabajo, citar una decisión o \
responder una pregunta concreta — leerá TU registro en su lugar, como si \
fuera la conversación misma. Por eso el registro no resume: reemplaza. El \
nombre "caja negra" no tiene más significado que este; no busques otro.
</que_es>

<reglas>
1. Nada inventado. Si algo no está en la conversación, no aparece. Si el \
lector necesitaría un dato que la conversación no da, se escribe la ausencia \
("no se especificó"), nunca un relleno plausible.
2. Citas literales. Toda cita va precedida de > y es copia exacta, sin \
retoque. Una cita retocada es una cita falsa.
3. Las incertidumbres se conservan. Lo que se dijo como hipótesis ("creo \
que", "quizá", "no sé si") no se convierte en hecho en el registro.
4. Cuasi-lossless: se comprime la FORMA, no el CONTENIDO. Puedes reformular, \
quitar cortesía, fundir repeticiones y saltarte el relleno. NO puedes omitir \
hechos, números, identificadores, nombres propios, rutas de archivo, comandos, \
mensajes de error, fechas, costos, ni matices de acuerdo y desacuerdo.
5. Los detalles técnicos no se omiten nunca.
6. La longitud la decide la conversación: una sesión densa pide un registro \
denso; una conversación corta rinde un registro corto. No hay tope. El \
registro está terminado cuando no queda información sin representar, no \
cuando alcanza cierta extensión. Un resumen decide qué es importante hoy; tú \
no puedes tomar esa decisión, porque el lector futuro necesita cosas que hoy \
parecen triviales. Ante la duda, se incluye.
7. El registro va en español. Las citas se citan en su idioma original.
8. Sin preámbulo, sin explicación de tu proceso, sin comentario final. Solo el \
registro.
</reglas>

<formato_de_caja>
La caja sigue EL formato oficial de caja negra Kairós — esta estructura \
exacta, con estos rótulos. El mapeo para este caso: la "sesión" es la \
CONVERSACIÓN; el "interlocutor" es el humano de la conversación; el \
"asistente" es la IA de esa conversación. Los corchetes son huecos que tú \
rellenas con lo de la conversación:

=== CAJA NEGRA KAIRÓS ===
ID: [AAAA-MM-DD de la conversación (o el rango si cruzó días)] / [título de \
la conversación] | CARGA: [dialéctica / pragmática / exploratoria / mixta — \
clasifica la conversación]
MARCO ACTIVO: [quién era el asistente de la conversación: léelo de ella misma \
— boot lines, firmas, estilo; si no consta, la plataforma: {fuente}]

## [A] VECTOR DE INICIO
### A.1 CONSULTA (literal, primer mensaje del humano)
> [copia exacta, sin paráfrasis ni edición]
### A.2 PREMISAS IMPLÍCITAS
[asunciones detectadas bajo la consulta — qué daba por supuesto el humano al \
empezar]
### A.3 DEFINICIONES
[términos acuñados o redefinidos en la conversación; si ninguno: ninguno]

## [B] RUTA DIALÉCTICA
NODO 1 — [hipótesis, idea o tensión que la conversación trabajó]
  └── falsación: [lo que la puso a prueba — respuesta, dato, contraejemplo, \
error] → [resistió / se modificó / cayó]
NODO [n] — [pivote o giro]
  └── detonante: [qué lo causó — un dato, una pregunta, un fallo detectado]
SÍNTESIS: [estado al cierre de la conversación]
  └── certeza: [provisional / verificada / especulativa]
  └── pendiente: [lo que quedó sin resolver]

[Tantos NODOS como la conversación merezca — cada uno autosuficiente; si un \
nodo cayó ante su falsación, se escribe.]

## [C] CEMENTERIO
1. [idea descartada durante la conversación] → razón del descarte.
[El cementerio evita que el lector futuro reabra lo ya cerrado; si no hubo \
descartes: nada]

## [D] REGISTRO DE ERRORES
1. [error de la IA de la conversación]: [qué pasó y por qué fue un error].
[Solo errores de la IA — alucinaciones, enlaces muertos, código que no corre, \
afirmaciones que la conversación misma corrigió. No errores del humano. Si \
ninguno: ninguno]

## [E] ACCIONABLE
### E.1 AXIOMA
[frase destilada de la conversación]
### E.2 TAREAS
[pasos concretos que la conversación dejó pendientes; si ninguno: ninguno]
### E.3 DELTA
[no aplica a conversaciones: escribe ninguno]
### E.4 ANCLAJES
[conceptos, autores, herramientas y datos que aparecieron y vale la pena \
retener]

=== FIN ===
</formato_de_caja>

<formato_de_respuesta>
Tu respuesta entera es la caja. Primera línea, exacta:
=== CAJA NEGRA KAIRÓS ===
Última línea, exacta:
=== FIN ===
Nada antes de la primera ni después de la última. Un programa detecta la caja \
por esas dos líneas: si no son exactas, la caja no existe.
</formato_de_respuesta>

<autochequeo>
Antes de cerrar la caja, verifica en silencio:
- ¿La primera línea es el marcador exacto y la última es === FIN ===?
- ¿Cada NODO declara su resultado (resistió / se modificó / cayó)?
- ¿Hay algún número, ruta, comando o identificador de la conversación que no \
aparezca en la caja?
- ¿Alguna cita está retocada?
- ¿Convertí alguna hipótesis en hecho?
- ¿El REGISTRO DE ERRORES habla solo de la IA de la conversación?
Si algo falla, corrígelo antes de responder.
</autochequeo>"""

# ─── Estado compartido (cola thread-safe; el resto bajo LOCK) ───
COLA = queue.Queue()
LOCK = threading.Lock()
EN_CURSO = set()          # archivos de inbox ya encolados (evita re-encolar)
ARCHIVOS_ABIERTOS = {}    # nombre → {"restantes": int, "errores": int}
ESTADO = {"hechas": 0, "ids_hechos": set(), "actual": None}


def log(msg):
    """Log doble: stdout (docker logs) + archivo de auditoría en estado/."""
    sello = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{sello}] {msg}", flush=True)
    try:
        ESTADO_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > LOG_MAX:
            LOG_FILE.rename(ESTADO_DIR /
                            f"black_boxer-{sello.replace(':', '').replace(' ', '_')}.log")
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{sello}] {msg}\n")
    except Exception:
        pass   # el archivo es auditoría; stdout ya salió


def _set_actual(titulo):
    with LOCK:
        ESTADO["actual"] = titulo


def _marca_hecho(note_id):
    with LOCK:
        ESTADO["hechas"] += 1
        ESTADO["ids_hechos"].add(note_id)


def _hash(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]


# ─── SQL directo (patrón daemon N1: escritura quirúrgica, sin cascada) ───
def _sql(query, params=None):
    conn = psycopg2.connect(**PG)
    try:
        cur = conn.cursor()
        cur.execute(query, params or ())
        out = cur.fetchall() if cur.description else []
        conn.commit()
        return out
    finally:
        conn.close()


def _escribir_caja(note_id, caja):
    # Sonda 30-sep: cero triggers en note_revised_current — UPDATE limpio.
    _sql("UPDATE note_revised_current SET content = %s WHERE note_id = %s",
         (caja, note_id))


# ─── Fortemi ───
def _get_nota(note_id):
    try:
        r = requests.get(f"{API}/notes/{note_id}", timeout=60)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Fortemi no responde: {e}")
    if r.status_code >= 400:
        raise RuntimeError(f"GET /notes/{note_id} {r.status_code}: {r.text[:200]}")
    return r.json()


def _nota_por_tag(tag):
    """note_id de la nota con ese tag, o None. 404 "tag not found" = cero
    resultados, no error (patrón verificado)."""
    try:
        r = requests.get(f"{API}/notes", params={"tags": tag, "limit": 1}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Fortemi no responde al consultar el tag {tag}: {e}")
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise RuntimeError(f"GET /notes {r.status_code}: {r.text[:200]}")
    notas = (r.json() or {}).get("notes") or []
    if not notas:
        return None
    n = notas[0]
    return n.get("id") or n.get("note_id")


def _reembed(note_id):
    """Vía quirúrgica verificada (sonda 30-sep): POST /jobs embedding para ESTA
    nota. El revised cambió; los vectores indexan el revised."""
    try:
        r = requests.post(f"{API}/jobs",
                          json={"job_type": "embedding", "note_id": note_id},
                          timeout=30)
        if r.status_code >= 400:
            log(f"aviso: re-embedding no encolado para {note_id} "
                f"(HTTP {r.status_code}) — la caja quedó escrita")
    except requests.exceptions.RequestException:
        log(f"aviso: Fortemi no respondió al re-embedding de {note_id} — "
            "la caja quedó escrita")


# ─── Generación ───
def _llm_caja(material, fuente):
    if not LLM_KEY:
        raise RuntimeError("falta TINFOIL_API_KEY — el bloque del servicio la inyecta")
    sistema = PROMPT_CAJA.replace("{fuente}", fuente)
    # Sándwich de tarea: tras el material largo, una línea que restate la tarea
    # (los primeros tokens pesan más, pero el material no debe enterrarla).
    usuario = (f"{material}\n\n---\n"
               "Genera ahora el registro de recuperación de esta conversación.")
    ultimo = ""
    for intento in range(1, INTENTOS_LLM + 1):
        try:
            r = requests.post(
                f"{LLM_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_KEY}"},
                json={"model": MODELO,
                      "reasoning_effort": REASONING_EFFORT,
                      "max_tokens": MAX_TOKENS_SALIDA,
                      "messages": [{"role": "system", "content": sistema},
                                   {"role": "user", "content": usuario}]},
                timeout=TIMEOUT_LLM)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
            choices = r.json().get("choices") or [{}]
            content = (choices[0].get("message") or {}).get("content") or ""
            if content.strip():
                return content.strip()
            raise RuntimeError("respuesta vacía (content null) — "
                               f"finish_reason={choices[0].get('finish_reason')}")
        except Exception as e:
            ultimo = str(e)
            if intento < INTENTOS_LLM:
                time.sleep(10)
    raise RuntimeError(ultimo)


def _valida(caja):
    """Fail-closed: sin marcador inicial y cierre, no se escribe nada."""
    if not caja.lstrip().startswith(MARCA_CAJA):
        raise RuntimeError("la caja no abre con el marcador. "
                           f"Primeros 200 chars:\n{caja[:200]}")
    if not caja.rstrip().endswith("=== FIN ==="):
        raise RuntimeError("la caja no cierra con === FIN ===. "
                           f"Últimos 200 chars:\n{caja[-200:]}")


# ─── Formato de la conversación ───
def _conv_a_md(conv):
    """Conversación del esquema v2 → .md nativo de Tinfoil
    (## User / ## Assistant, turnos separados por ---). El razonamiento interno
    y los adjuntos también son contenido: se conservan marcados."""
    partes = []
    for m in conv.get("chat_messages", []):
        rol = "User" if m.get("sender") == "human" else "Assistant"
        cuerpo = (m.get("text") or "").strip()
        for c in (m.get("content") or []):
            if c.get("type") == "thinking" and (c.get("thinking") or "").strip():
                cuerpo += ("\n\n" if cuerpo else "") + \
                    "[razonamiento interno de la IA]\n" + c["thinking"].strip()
        for a in (m.get("attachments") or []):
            if a.get("type") == "document":
                cuerpo += ("\n\n" if cuerpo else "") + \
                    f"[adjunto: {a.get('fileName', '?')}]\n" + (a.get("textContent") or "")
            else:
                # image y lo que venga: se anota, no se transcribe.
                cuerpo += ("\n\n" if cuerpo else "") + \
                    f"[adjunto no textual: {a.get('type', '?')} — {a.get('fileName', '?')}]"
        partes.append(f"## {rol}\n\n{cuerpo.strip()}")
    return "\n\n---\n\n".join(partes)


def _material(item):
    """Lo que recibe la LLM: encabezado (título/fecha/proyecto) + la cruda."""
    cab = [f"# {item['titulo']}", f"fecha: {item.get('fecha') or '(no consta)'}"]
    if item.get("proyecto"):
        cab.append(f"proyecto: {item['proyecto']}")
    return "\n".join(cab) + "\n\n" + item["md"]


# ─── Los dos trabajos del worker ───
def procesar_querier(note_id):
    """Nota ya en Fortemi sin caja. El material es SIEMPRE la cruda (original),
    que ya está en .md nativo — nunca el molde."""
    d = _get_nota(note_id)
    original = (d.get("original") or {}).get("content") or ""
    revised = (d.get("revised") or {}).get("content") or ""
    if MARCA_CAJA in revised:
        log(f"ya tiene caja, no se toca: {note_id}")
        _marca_hecho(note_id)
        return
    if not original.strip():
        raise RuntimeError(f"{note_id} sin original")
    nota = d.get("note", {})
    tags = {t.lower() for t in d.get("tags", [])}
    fuente = next((f for f in FUENTES_CONV if f in tags), "fuente-desconocida")
    fecha = (nota.get("created_at_utc") or "")[:10] or "(no consta)"
    material = (f"# {nota.get('title') or '(sin título)'}\nfecha: {fecha}\n\n{original}")
    t0 = time.time()
    caja = _llm_caja(material, fuente)
    _valida(caja)
    _escribir_caja(note_id, caja)
    _reembed(note_id)
    _marca_hecho(note_id)
    log(f"caja escrita: {nota.get('title') or note_id} · {len(caja):,} chars · "
        f"{len(original):,} chars crudos · {time.time()-t0:.0f}s · [querier]")


def procesar_import(item):
    """Conversación nueva (.md nativo o .json parseado a .md). Fail-closed: la
    caja valida ANTES de crear la nota — un fallo no deja nada a medias. Si la
    nota ya existe sin caja (crash anterior), se le genera; no se duplica."""
    titulo = item["titulo"]
    nid_existente = None
    if item.get("dedupe"):
        nid_existente = _nota_por_tag(item["dedupe"])
        if nid_existente:
            d = _get_nota(nid_existente)
            if MARCA_CAJA in ((d.get("revised") or {}).get("content") or ""):
                log(f"omitida (ya en Fortemi con caja): {titulo}")
                return
            log(f"recuperando: {titulo} ya existe sin caja ({nid_existente}) — se genera")
    t0 = time.time()
    caja = _llm_caja(_material(item), "tinfoil")
    _valida(caja)
    if nid_existente:
        note_id = nid_existente
    else:
        r = requests.post(f"{API}/notes", json={
            "content": item["md"], "pipeline": [], "revision_mode": "none",
            "document_type": "ai-conversation"}, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"POST /notes {r.status_code}: {r.text[:200]}")
        note_id = r.json()["id"]
        # PUT de tags REEMPLAZA el set entero: nota nueva, lista completa.
        for intento in (1, 2):
            rt = requests.put(f"{API}/notes/{note_id}/tags",
                              json={"tags": item["tags"]}, timeout=30)
            if rt.status_code < 400:
                break
            if intento == 2:
                log(f"aviso: tags no aplicados a {titulo} ({rt.status_code}) — "
                    "una re-solta podría no deduplicar")
        # Título y fecha reales por SQL, verbatim.
        _sql("UPDATE note SET title = %s WHERE id = %s", (titulo, note_id))
        if item.get("fecha"):
            _sql("UPDATE note SET created_at_utc = %s WHERE id = %s",
                 (item["fecha"], note_id))
    _escribir_caja(note_id, caja)
    _reembed(note_id)
    _marca_hecho(note_id)
    log(f"caja escrita: {titulo} → nota {note_id} · {len(caja):,} chars · "
        f"{time.time()-t0:.0f}s · [{item['archivo']}]")


# ─── Worker serial ───
def _archivo_listo(nombre):
    """Una conversación menos; a cero, el archivo se mueve a procesados/."""
    with LOCK:
        info = ARCHIVOS_ABIERTOS.get(nombre)
        if not info:
            return
        info["restantes"] -= 1
        if info["restantes"] > 0:
            return
        del ARCHIVOS_ABIERTOS[nombre]
    EN_CURSO.discard(nombre)
    try:
        shutil.move(str(INBOX / nombre), str(PROCESADOS / nombre))
        log(f"archivo terminado: {nombre} · err={info['errores']} → procesados/")
    except Exception as e:
        # El archivo queda en inbox: la próxima pasada re-dedupe (sin gastar
        # DeepSeek) y reintenta el move. Inofensivo y se auto-repara.
        log(f"aviso: no pude mover {nombre} a procesados ({e})")


def worker():
    while True:
        item = COLA.get()
        try:
            if item["tipo"] == "import":
                _set_actual(item.get("titulo") or "(sin nombre)")
                procesar_import(item)
            else:
                try:
                    d = _get_nota(item["note_id"])
                    titulo = (d.get("note", {}) or {}).get("title") or item["note_id"]
                except Exception:
                    titulo = item["note_id"]
                _set_actual(titulo)
                procesar_querier(item["note_id"])
        except Exception as e:
            log(f"ERROR [{item['tipo']}] "
                f"{item.get('titulo') or item.get('note_id')}: {e}")
            if item.get("archivo"):
                with LOCK:
                    info = ARCHIVOS_ABIERTOS.get(item["archivo"])
                    if info:
                        info["errores"] += 1
        finally:
            _set_actual(None)
            if item.get("archivo"):
                _archivo_listo(item["archivo"])


# ─── Vigilancia de carpeta (patrón N1: sondeo, no notificaciones del kernel) ───
def wait_transfer(path):
    """Mover entre carpetas de Windows no es atómico visto desde Linux."""
    prev = -1
    for _ in range(20):
        try:
            actual = path.stat().st_size
        except FileNotFoundError:
            return False
        if actual == prev and actual > 0:
            return True
        prev = actual
        time.sleep(2)
    return False


def _a_fallados(ruta, e):
    shutil.move(str(ruta), str(FALLADOS / ruta.name))
    (FALLADOS / f"{ruta.stem}.error.txt").write_text(str(e), encoding="utf-8")
    log(f"archivo a fallados: {ruta.name} — {e}")


def _encolar_md(ruta):
    """.md nativo de Tinfoil: ya es el formato que recibe la LLM — no se parsea."""
    try:
        md = ruta.read_text(encoding="utf-8").strip()
        if not md:
            raise ValueError("archivo vacío")
    except Exception as e:
        _a_fallados(ruta, e)
        return
    EN_CURSO.add(ruta.name)
    with LOCK:
        ARCHIVOS_ABIERTOS[ruta.name] = {"restantes": 1, "errores": 0}
    log(f"archivo a la cola: {ruta.name} · 1 conversación (.md, sin parsear)")
    COLA.put({"tipo": "import", "titulo": ruta.stem, "fecha": None, "md": md,
              "tags": ["tinfoil", f"tmd/{_hash(md)}"],
              "dedupe": f"tmd/{_hash(md)}", "archivo": ruta.name})


def _encolar_json(ruta):
    """.json (varias conversaciones): cada una se parsea a .md."""
    try:
        convs = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(convs, list):
            raise ValueError("no es un array de conversaciones")
        if not convs:
            raise ValueError("array vacío")
    except Exception as e:
        _a_fallados(ruta, e)
        return
    EN_CURSO.add(ruta.name)
    with LOCK:
        ARCHIVOS_ABIERTOS[ruta.name] = {"restantes": len(convs), "errores": 0}
    log(f"archivo a la cola: {ruta.name} · {len(convs)} conversaciones (.json → .md)")
    for conv in convs:
        titulo = conv.get("name") or "(sin nombre)"
        fecha = (conv.get("created_at") or "")[:10] or None
        uuid_conv = (conv.get("uuid") or "").lower()
        dedupe = f"tconv/{uuid_conv}" if uuid_conv else None
        tags = ["tinfoil"] + ([dedupe] if dedupe else [])
        proyecto = conv.get("projectId")
        if proyecto:
            tags.append(f"tproj/{proyecto}".lower())
        COLA.put({"tipo": "import", "titulo": titulo, "fecha": fecha,
                  "md": _conv_a_md(conv), "tags": tags, "dedupe": dedupe,
                  "proyecto": proyecto, "archivo": ruta.name})


def vigilar():
    while True:
        activo = False
        for ruta in sorted(INBOX.iterdir()):
            if ruta.is_dir() or ruta.name.startswith("."):
                continue
            ext = ruta.suffix.lower()
            if ext not in (".md", ".json"):
                continue
            if ruta.name in EN_CURSO:
                continue
            if not wait_transfer(ruta):
                continue
            activo = True
            if ext == ".md":
                _encolar_md(ruta)
            else:
                _encolar_json(ruta)
        time.sleep(POLL_CORTO if activo else POLL_LARGO)


# ─── HTTP interno ───
class Handler(BaseHTTPRequestHandler):
    def _responde(self, code, obj):
        cuerpo = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_POST(self):
        if self.path != "/cajas":
            self._responde(404, {"error": "solo POST /cajas"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._responde(400, {"error": "JSON invalido"})
            return
        ids = [i for i in (data.get("note_ids") or []) if i]
        if not ids:
            self._responde(400, {"error": "note_ids vacio"})
            return
        for nid in ids:
            COLA.put({"tipo": "querier", "note_id": nid})
        self._responde(200, {"encoladas": len(ids), "en_cola": COLA.qsize()})

    def do_GET(self):
        if self.path == "/salud":
            self._responde(200, {"ok": True, "en_cola": COLA.qsize()})
            return
        if self.path.startswith("/estado"):
            ids = []
            if "?" in self.path:
                qs = parse_qs(urlparse(self.path).query)
                ids = [i for i in (qs.get("ids", [""])[0].split(",")) if i]
            with LOCK:
                hechas = ESTADO["hechas"]
                actual = ESTADO["actual"]
                hechos_set = set(ESTADO["ids_hechos"])
            pendientes = len([i for i in ids if i not in hechos_set]) if ids else None
            self._responde(200, {"ok": True, "en_cola": COLA.qsize(),
                                 "en_curso": actual, "hechas": hechas,
                                 "pendientes": pendientes})
            return
        self._responde(404, {"error": "GET /salud | GET /estado"})

    def log_message(self, *args):
        pass   # el access-log ruidoso fuera; lo que importa pasa por log()


def servir_http():
    ThreadingHTTPServer(("0.0.0.0", PUERTO), Handler).serve_forever()


def main():
    for d in (INBOX, PROCESADOS, FALLADOS, ESTADO_DIR):
        d.mkdir(parents=True, exist_ok=True)
    log("Black Boxer arriba.")
    log(f"  carpeta: {INBOX}  (acepta .md y .json)")
    log(f"  modelo: {MODELO} · effort: {REASONING_EFFORT} · LLM: {LLM_URL}")
    log(f"  HTTP interno en puerto {PUERTO} (POST /cajas · GET /estado · GET /salud)")
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=servir_http, daemon=True).start()
    try:
        vigilar()
    except KeyboardInterrupt:
        log("daemon detenido.")


if __name__ == "__main__":
    main()
