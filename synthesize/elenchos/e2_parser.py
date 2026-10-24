#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
e2_parser.py — etapa 7. Materializa en Fortemi lo que sobrevivio al debate.

Determinista en lo mecanico, agentico solo donde hace falta juicio.

QUE HACE
--------
  1. Lee las hipotesis del debate.
  2. Absorbe los duplicados senalados con duplicate_of.
  3. Para cada hipotesis que se escribe:
       - busca si ya existe una equivalente en Fortemi (busqueda hibrida)
       - si existe  -> PATCH anadiendo un bloque UPDATE al final. Nunca borra.
       - si no      -> POST nota nueva con tag thesis + H-NNN
       - adjunta el concept de estatus del scheme thesis-status
       - etiqueta cada nota citada con cited/H-NNN
       - crea o encuentra la collection del tema y mueve alli lo que toco
  4. Escribe un resumen json de todo lo que hizo.

DECISIONES QUE ESTAN CABLEADAS AQUI Y POR QUE
---------------------------------------------
* SOLO SE ANADE, NUNCA SE BORRA. PATCH /api/v1/notes/{id} reemplaza el content
  entero, asi que se lee lo que hay, se concatena y se manda completo. Los tags
  tambien se reemplazan: se leen antes y se anaden a los existentes. Si alguna
  vez este script borra algo, es un bug.

* LA CITA VA COMO TAG, NO SOLO COMO TEXTO. Cada nota citada recibe cited/H-NNN.
  Sin eso, "dame las notas que ninguna hipotesis ha referenciado" obliga a leer
  todas las hipotesis y cruzar a mano contra miles de notas, cada semana. Con el
  tag es un filtro de la API. Cuesta lo mismo hacerlo ahora.

* LA COLLECTION CRECE, NO SE COMPLETA. No es requisito que un tema tenga desde
  el dia uno todas las notas del corpus que le pertenecen. El parser mete lo que
  el proceso ya toco: las hipotesis y las notas que citaron. El resto del
  material entra cuando el analisis lo use — otra corrida de E2 que lo cite, o
  N4 buscando. La organizacion pasa mientras se investiga, no antes y no en una
  pasada aparte. No hay aqui ningun mecanismo pendiente que llene el hueco,
  porque no hay hueco.

* UNA NOTA DE DOS TEMAS NO SE REPARTE: VA AL TEMA HIBRIDO. Fortemi permite una
  sola collection por nota. Si la nota X vive en "fotografia" y ahora una
  hipotesis de "viajes" la cita, NO se mueve de fotografia a viajes — se
  mueve a "fotografia de viaje", que es el tema que de verdad la contiene.
  La collection hibrida no es un parche a la restriccion de Fortemi: es el tema
  emergente, y existe porque el material lo produjo.
  Encadena: si esa nota luego la cita una hipotesis de un tercer tema, el
  hibrido crece. Los componentes quedan escritos en la descripcion de la
  collection, asi que el mismo par siempre resuelve a la misma collection entre
  corridas.
  Nombrar el hibrido es juicio ("fotografia" + "viajes" -> "fotografia de
  viaje", no "fotografia y viajes"), asi que lo nombra el LLM. Encontrarlo,
  crearlo y mover, lo hace este codigo.

* FUSION != AGRUPACION. Solo se fusionan las que el debate marco duplicate_of.
  Las complementarias — mismo tema, distinto angulo — no se tocan: comparten
  collection. Fusionar destruye fronteras; agrupar no destruye nada y se puede
  deshacer.

* EL TEXTO LITERAL DE LAS NOTAS VIAJA CON LA HIPOTESIS. Sin eso N4 tendria que
  ir nota por nota a buscar de que habla cada id.

USO
---
    python /app/e2_parser.py --run-dir /app/corpus/run_<periodo> --period <periodo> --dry-run
    python /app/e2_parser.py --run-dir /app/corpus/run_<periodo> --period <periodo>
"""

from __future__ import annotations
import argparse, json, os, re, sys, time, unicodedata
from datetime import datetime, timezone
import importlib.util as _ilu
_spec_cc = _ilu.spec_from_file_location("_concatenacion",
    __import__("pathlib").Path(__file__).resolve().parent / "_concatenacion.py")
_cc = _ilu.module_from_spec(_spec_cc); _spec_cc.loader.exec_module(_cc)
from pathlib import Path
from typing import Any

import requests

# ── Configuracion ─────────────────────────────────────────────────────────────
# Mismo nombre de variable y mismo valor por defecto que usa e2_debate.py.
REST = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000").rstrip("/")
CLIENT_ID = os.environ.get("MCP_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("MCP_CLIENT_SECRET", "")
TIMEOUT = 60

# Ids del scheme y del concept. El scheme se creo una vez, antes del primer
# parser, y su titulo mostrado es "Thesis Status", no "thesis-status": buscarlo
# por comparacion exacta de cadena no lo encontraba y el parser intentaba crear
# uno nuevo (422 por falta de `notation`). Se usan los ids directamente; los de
# aqui son de ejemplo y se sobreescriben con N3_SCHEME_ID.
SCHEME_ID = os.environ.get("N3_SCHEME_ID", "00000000-0000-7000-8000-000000000001")
CONCEPT_HYPOTHESIS_ID = "00000000-0000-7000-8000-000000000002"
SCHEME_TITULOS = ("thesis status", "thesis-status", "thesisstatus")

# Lista de features de la cascada que SI se quieren. Ninguna de las seis que
# Fortemi filtra, para que no corra ni revision ni title_generation ni
# concept_tagging; embedding y linking van aparte y no dependen de esto.
# Con [] Fortemi no procesaria NADA (ni embedding ni linking): por eso no va
# vacia. Se puede sobreescribir con N3_PIPELINE si algun dia hace falta.
PIPELINE_MINIMO = [x for x in os.environ.get("N3_PIPELINE", "embedding").split(",") if x]

# Estatus del debate -> concept del scheme.
# hypothesis ya implica haber sobrevivido demolicion (por eso `active` se
# elimino en su dia). falsified aqui lleva su procedencia en el trazo: no es lo
# mismo que un agente conceda a que lo tumbes tu en N4, y el contenido lo dice.
ESTATUS = {
    "activo":         "hypothesis",
    "refinada":       "hypothesis",
    "defendida":      "hypothesis",
    "falsado":        "falsified",
    "sin_responder":  "unanswered",
    "deprecated":     "deprecated",
}
# `nuevo` no se escribe: nadie la examino.
NO_ESCRIBIR = {"nuevo"}

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                     r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

TOKEN = {"valor": None, "expira": 0.0}
RESUMEN: dict[str, Any] = {
    "periodo": None, "dry_run": False, "creadas": [], "actualizadas": [],
    "absorbidas": [], "omitidas": [], "collections": [], "notas_etiquetadas": 0,
    "migraciones": [], "hibridos": [], "notas_de_dos_temas": [], "collections_resueltas": [], "consolidaciones": [], "fusiones_llm": [], "fusiones_estatus_mixto": [], "confusiones_con_heredadas": [], "emit_collection": [],
    "collections_renombradas": [], "pendientes": [], "errores": [],
}


def log(m: str) -> None:
    print(f"[parser] {m}", flush=True)


def sanear_tag(t: str) -> str:
    """Sin espacios, solo alfanumericos, guiones, guion bajo y barra."""
    t = unicodedata.normalize("NFKD", str(t))
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-zA-Z0-9_/-]", "-", t).strip("-")
    return re.sub(r"-{2,}", "-", t)


# ══════════════════════════════════════════════════════════════════════════════
# CLIENTE REST
# ══════════════════════════════════════════════════════════════════════════════

def token() -> str | None:
    if not CLIENT_ID or not CLIENT_SECRET:
        return None
    if TOKEN["valor"] and time.time() < TOKEN["expira"] - 60:
        return TOKEN["valor"]
    r = requests.post(f"{REST}/oauth/token", timeout=TIMEOUT,
                      data={"grant_type": "client_credentials",
                            "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
    r.raise_for_status()
    d = r.json()
    TOKEN["valor"] = d.get("access_token")
    TOKEN["expira"] = time.time() + float(d.get("expires_in", 3600))
    return TOKEN["valor"]


# ══════════════════════════════════════════════════════════════════════════════
# REGLA DE ORO
# ══════════════════════════════════════════════════════════════════════════════
# ESCRIBIR TAGS ES DESTRUCTIVO. Verificado en el codigo de Fortemi:
#
#   PUT   /api/v1/notes/{id}/tags  -> set_note_tags -> set_for_note_tx
#   PATCH /api/v1/notes/{id}       -> update_note, y SOLO si el body trae "tags"
#                                     -> el mismo set_for_note_tx
#   set_for_note_tx  =  DELETE FROM note_tag WHERE note_id = $1  +  INSERT
#
# O sea: cualquier escritura de tags reemplaza el set entero. Mandar un tag
# suelto borra todos los demas. Asi se perdieron los tags de varias notas.
#
# La regla, entonces: NUNCA se escribe una lista de tags que no sea
# (los que la nota ya tiene) + (los nuevos). Y si los existentes no se pudieron
# leer, no se escribe nada.
#
# Aqui no queda como buena intencion: el cliente rechaza cualquier peticion que
# lleve "tags" en el cuerpo si no viene marcada como union verificada. Si alguien
# (yo, mañana) escribe un PUT de tags a mano, revienta en vez de borrar.
#
# LOS SKOS SON OTRA COSA Y NO SE TOCAN:
#   POST /api/v1/notes/{id}/concepts -> tag_note_tx
#        -> INSERT ... ON CONFLICT (note_id, concept_id) DO UPDATE
#   Es aditivo. Nunca borra. Adjuntar `hypothesis` no roza los demas conceptos.
#   (replace_note_tags, que si hace DELETE FROM note_skos_concept, no la llama
#    ningun endpoint: es codigo muerto en esta version.)
#   POST /api/v1/notes/{id}/move solo cambia collection_id.

def api(metodo: str, ruta: str, union_verificada: bool = False, **kw) -> Any:
    cuerpo = kw.get("json") or {}
    if isinstance(cuerpo, dict) and "tags" in cuerpo and not union_verificada:
        raise RuntimeError(
            f"BLOQUEADO: {metodo} {ruta} lleva 'tags' sin union verificada. "
            "Escribir tags reemplaza el set entero y borraria los existentes. "
            "Usa anadir_tags(), que lee los actuales y escribe la union.")
    # GET /api/v1/notes?collection_id= ACEPTA el parametro y NO LO APLICA.
    # Verificado en el codigo de Fortemi: ListNotesQuery tiene el campo y main.rs
    # lo copia al request, pero bind_list_request_params! solo liga tags y las
    # cuatro fechas -- collection_id nunca llega al SQL. La consulta devuelve el
    # corpus entero (default 50, tope 100, orden created_at DESC) sin dar error.
    # Confiar en ese filtro para MOVER notas arrastro 100 notas ajenas a una
    # collection. No vuelve a pasar: aqui se rechaza.
    params = kw.get("params") or {}
    if (isinstance(params, dict) and "collection_id" in params
            and ruta.rstrip("/").endswith("/api/v1/notes")):
        raise RuntimeError(
            f"BLOQUEADO: {metodo} {ruta} filtra por collection_id, y ese filtro "
            "NO se aplica en Fortemi: devolveria el corpus entero en silencio. "
            "Usa GET /api/v1/collections/{id}/notes.")
    h = kw.pop("headers", {}) or {}
    t = token()
    if t:
        h["Authorization"] = f"Bearer {t}"
    url = ruta if ruta.startswith("http") else f"{REST}{ruta}"
    r = requests.request(metodo, url, headers=h, timeout=TIMEOUT, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{metodo} {ruta} -> {r.status_code}: {r.text[:300]}")
    if r.status_code == 204 or not (r.text or "").strip():
        return None
    try:
        return r.json()
    except Exception:
        return r.text


# ── Envolturas concretas ──────────────────────────────────────────────────────

def get_note(nid: str) -> dict | None:
    """OJO: GET /api/v1/notes/{id} devuelve un SOBRE, no la nota:
        {"note": {...}, "original": {"content": ...}, "revised": {...},
         "tags": [...], "concepts": [...], "links": [...]}
    El contenido vive en original.content y los tags al nivel de arriba. Devolver
    solo el objeto "note" hacia dentro dejaba content y tags vacios — y como PUT
    de tags reemplaza el set entero, leerlos vacios BORRA los tags de la nota.
    Aqui se devuelve el sobre aplanado en las claves que se usan."""
    try:
        d = api("GET", f"/api/v1/notes/{nid}") or {}
        nota = dict(d.get("note") or {})
        rev = (d.get("revised") or {}).get("content")
        orig = (d.get("original") or {}).get("content")
        nota["content"] = orig or rev or nota.get("content") or ""
        nota["content_revised"] = rev or ""
        nota["tags"] = d.get("tags") or nota.get("tags") or []
        nota["concepts"] = d.get("concepts") or []
        return nota
    except Exception as e:
        RESUMEN["errores"].append(f"get_note {nid}: {e}")
        return None


def notas_por_tag(tag: str, limite: int = 500) -> list[dict]:
    """Pagina de verdad. GET /api/v1/notes aplica

        let limit = req.limit.unwrap_or(50).min(100)

    asi que pedir limit=500 devuelve 100 y no avisa. Sin paginar, esto es una
    bomba de relojeria: siguiente_numero() calcula el proximo H-NNN a partir de
    aqui, y en cuanto haya mas de 100 hipotesis empezaria a reutilizar numeros
    que ya existen, en silencio. Hoy hay 6, por eso no se ha notado.
    """
    todas: list[dict] = []
    offset = 0
    while len(todas) < limite:
        pedir = min(100, limite - len(todas))
        try:
            d = api("GET", "/api/v1/notes",
                    params={"tags": tag, "limit": pedir, "offset": offset})
        except Exception as e:
            RESUMEN["errores"].append(f"notas_por_tag {tag} (offset {offset}): {e}")
            break
        if isinstance(d, dict):
            lote = d.get("notes") or d.get("results") or []
        else:
            lote = d or []
        if not lote:
            break
        todas.extend(lote)
        if len(lote) < pedir:
            break
        offset += len(lote)
    return todas


def buscar_hibrido(consulta: str, limite: int = 8) -> list[dict]:
    """GET /api/v1/search?q=...&mode=hybrid. Devuelve .results[] con note_id.
    No acepta comodin: q='*' no vale."""
    consulta = " ".join(str(consulta).split())[:400]
    if not consulta:
        return []
    try:
        d = api("GET", "/api/v1/search", params={
            "q": consulta, "mode": "hybrid", "limit": limite,
            "strict_filter": json.dumps({"required_tags": ["thesis"]})})
        return (d or {}).get("results", []) if isinstance(d, dict) else (d or [])
    except Exception as e:
        # 404 "Required tag not found" = el tag thesis aun no existe en Fortemi.
        # Es la primera corrida, no un fallo: no hay contra que comparar.
        if "404" in str(e) and "tag not found" in str(e).lower():
            return []
        RESUMEN["errores"].append(f"search: {e}")
        return []


def crear_nota(contenido: str, tags: list[str]) -> str | None:
    # POST /api/v1/notes NO acepta title: el body solo admite content, tags y
    # revision_mode, y el titulo lo genera la cascada. Por eso el H-NNN va en el
    # tag y en la primera linea del contenido ("# H-001 — tema"), que es de donde
    # el generador de titulos lo tomara si corre.
    # LA CASCADA DE FORTEMI, ACOTADA A PROPOSITO.
    #
    # Verificado en queue_nlp_pipeline_inner (crates/matric-api/src/main.rs):
    #   "When `pipeline` is Some, only listed features are queued.
    #    Empty = no processing. When None, full default pipeline runs."
    #
    # Las features que ese filtro controla son exactamente seis:
    #   revision, title_generation, reference_extraction, metadata_extraction,
    #   document_type_inference, concept_tagging
    #
    # Embedding y Linking NO pasan por ese filtro: se encolan aparte al terminar
    # la fase 1 (handlers/jobs.rs, "Embedding and Linking are tier-agnostic").
    # Asi que una lista que no contenga ninguna de las seis apaga toda la IA y
    # deja pasar embedding y linking, que es justo lo que se quiere aqui.
    #
    # POR QUE. Estas notas las escribe el parser con su contenido ya decidido:
    # el titulo, el estado y las relaciones salen del debate. Que Fortemi les
    # pase ai_revision, les reescriba el titulo o les invente concepts encima
    # es contaminar un resultado con una capa que nadie pidio -- y ademas cuesta
    # dinero y cola de trabajos por cada nota. Lo unico que se quiere de la
    # cascada es que la nota sea buscable (embedding) y quede conectada
    # (linking).
    payload = {"content": contenido, "tags": [sanear_tag(t) for t in tags],
               "revision_mode": "none",
               "pipeline": PIPELINE_MINIMO}
    # Nota nueva: no hay tags previos que perder.
    d = api("POST", "/api/v1/notes", json=payload, union_verificada=True)
    return (d or {}).get("id") or ((d or {}).get("note") or {}).get("id")


def leer_tags(nid: str) -> list[str] | None:
    """Endpoint dedicado: GET /api/v1/notes/{id}/tags -> ["tag1","tag2"].
    Se usa este y no el sobre de la nota, porque es la fuente directa."""
    try:
        d = api("GET", f"/api/v1/notes/{nid}/tags")
    except Exception as e:
        RESUMEN["errores"].append(f"leer_tags {nid}: {e}")
        return None
    if isinstance(d, dict):
        d = d.get("tags")
    if not isinstance(d, list):
        return None
    return [x.get("name", x) if isinstance(x, dict) else str(x) for x in d]


def anadir_tags(nid: str, nuevos: list[str]) -> bool:
    """PUT reemplaza el SET ENTERO ("Set (reemplaza)"). Si la lectura de los
    tags existentes falla, NO se escribe: escribir con una lectura vacia borra
    todos los tags de la nota. Es preferible no etiquetar a destruir."""
    actuales = leer_tags(nid)
    if actuales is None:
        RESUMEN["errores"].append(
            f"anadir_tags {nid}: no se pudieron leer los tags actuales. No se escribe.")
        return False
    limpios = {sanear_tag(x) for x in actuales if str(x).strip()}
    union = sorted(limpios | {sanear_tag(x) for x in nuevos})
    if set(union) == limpios:
        return True
    if len(union) < len(limpios):
        RESUMEN["errores"].append(f"anadir_tags {nid}: la union perderia tags. Abortado.")
        return False
    try:
        api("PUT", f"/api/v1/notes/{nid}/tags", json={"tags": union}, union_verificada=True)
    except Exception as e:
        # Una nota citada puede haberse borrado o movido despues de que la
        # hipotesis la citara. Fortemi responde 400 "Referenced note or tag not
        # found". Eso no debe tumbar la hipotesis entera: el bloque ya se
        # escribio y lo unico que se pierde es un tag `cited/` sobre una nota
        # que ya no existe. Se anota y se sigue.
        RESUMEN["errores"].append(f"anadir_tags {nid}: {e}")
        return False
    return True


def anadir_parrafo(nid: str, bloque: str) -> bool:
    """Anade un bloque al contenido de una nota existente.

    PATCH /notes/{id} con content dispara el pipeline NLP completo en esta
    version de Fortemi (v2026.7.1). UpdateNoteBody no tiene campo `pipeline`. La
    funcion interna lo recibe fijo como None (cascada completa). [Verificado en
    main.rs update_note handler, linea 13528-13542]

    Para escribir sin cascada se usa SQL directo a note_original, que es
    atomico y no encola ningun job. [Verificado en update_original_tx,
    notes.rs linea 1099-1125]
    """
    n = get_note(nid)
    if n is None:
        return False
    previo = n.get("content") or ""
    nuevo = previo.rstrip() + "\n\n" + bloque
    # SQL directo: sin cascada
    if _sql_content_update(nid, nuevo):
        return True
    # Fallback a PATCH si el SQL no esta disponible (p.ej. en tests)
    api("PATCH", f"/api/v1/notes/{nid}", json={"content": nuevo})
    return True


def _sql_content_update(note_id: str, content: str) -> bool:
    """Actualiza note_original.content via psycopg2, sin disparar cascada.

    PATCH /notes/{id} con content siempre dispara el pipeline NLP completo
    en esta version de Fortemi. La unica via sin cascada es SQL directo a
    note_original. [Verificado en main.rs update_note handler]

    Usa psycopg2 directo a workstation-postgres (visible por nombre de servicio
    dentro del stack Docker Compose). Credenciales del docker-compose.yml:
    user=matric, password=${POSTGRES_PASSWORD:-fortemi-local-dev}, db=matric.
    """
    try:
        import os, psycopg2
        host = os.environ.get("POSTGRES_HOST", "workstation-postgres")
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
        user = os.environ.get("POSTGRES_USER", "matric")
        pwd  = os.environ.get("POSTGRES_PASSWORD", "fortemi-local-dev")
        db   = os.environ.get("POSTGRES_DB", "matric")
        conn = psycopg2.connect(host=host, port=port, user=user, password=pwd, dbname=db)
        conn.autocommit = False
        ts = datetime.now(timezone.utc)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE note_original SET content = %s, user_last_edited_at = %s "
                "WHERE note_id = %s::uuid",
                (content, ts, note_id))
            cur.execute(
                "UPDATE note SET updated_at_utc = %s WHERE id = %s::uuid",
                (ts, note_id))
        conn.commit()
        conn.close()
        return True
    except ImportError:
        # psycopg2 no disponible (tests locales fuera del contenedor)
        return False
    except Exception as e:
        try:
            RESUMEN["errores"].append(f"SQL content {note_id}: {e}")
        except Exception:
            pass
        return False

# ── Concepts SKOS ─────────────────────────────────────────────────────────────

def _norm(s: str) -> str:
    """Compara ignorando mayusculas, tildes, espacios y guiones. 'Thesis Status',
    'thesis-status' y 'thesis status' son lo mismo; dos grafias del mismo nombre
    tambien. Sin esto Fortemi acaba con la misma coleccion dos veces."""
    s = unicodedata.normalize("NFKD", str(s or "").strip().lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[\s_-]+", " ", s).strip()


def scheme_id() -> str | None:
    """NUNCA crea a ciegas. El scheme ya existe; crear otro duplicaria el
    vocabulario y dejaria las hipotesis sin estatus."""
    try:
        d = api("GET", "/api/v1/concepts/schemes")
        lista = d.get("schemes", d) if isinstance(d, dict) else (d or [])
        for s in lista:
            if s.get("id") == SCHEME_ID:
                return SCHEME_ID
        for s in lista:
            if _norm(s.get("title") or s.get("name")) in SCHEME_TITULOS:
                log(f"scheme encontrado por titulo: {s.get('title')} ({s.get('id')})")
                return s.get("id")
        RESUMEN["errores"].append(
            "No aparece el scheme de estatus. NO se crea uno nuevo: revisa "
            f"GET /api/v1/concepts/schemes y pasa el id con N3_SCHEME_ID. "
            f"Esperado: {SCHEME_ID}")
        return None
    except Exception as e:
        RESUMEN["errores"].append(f"scheme: {e}")
        return None


_CONCEPTS: dict[str, str] = {}

_DEFINICIONES = {
    "hypothesis": "Patron emergido del debate que ha sobrevivido a la demolicion.",
    "falsified":  "Cayo ante contra-evidencia. Queda como registro, y es reversible.",
    "unanswered": "Fue atacada y el debate cerro antes de la respuesta. Pendiente de N4.",
    "deprecated": "Absorbida por otra hipotesis que dice lo mismo.",
}


def concept_id(nombre: str, sid: str) -> str | None:
    """Busca primero, siempre. `hypothesis` ya existe en el scheme."""
    if nombre in _CONCEPTS:
        return _CONCEPTS[nombre]
    if _norm(nombre) == "hypothesis":
        _CONCEPTS[nombre] = CONCEPT_HYPOTHESIS_ID
        return CONCEPT_HYPOTHESIS_ID
    try:
        d = api("GET", "/api/v1/concepts", params={"scheme_id": sid, "limit": 200})
        lista = d.get("concepts", d) if isinstance(d, dict) else (d or [])
        for c in lista:
            if _norm(c.get("pref_label")) == _norm(nombre) or _norm(c.get("notation")) == _norm(nombre):
                _CONCEPTS[nombre] = c["id"]
                return c["id"]
        # Crear exige `notation` (422 sin el). Verificado contra la API.
        d = api("POST", "/api/v1/concepts",
                json={"scheme_id": sid, "pref_label": nombre, "notation": nombre,
                      "definition": _DEFINICIONES.get(nombre, f"Estado {nombre}.")})
        cid = (d or {}).get("id")
        if cid:
            _CONCEPTS[nombre] = cid
            log(f"  concept nuevo en el scheme: {nombre} ({cid})")
        return cid
    except Exception as e:
        RESUMEN["errores"].append(f"concept {nombre}: {e}")
        return None


def adjuntar_concept(nid: str, cid: str) -> None:
    try:
        api("POST", f"/api/v1/notes/{nid}/concepts", json={"concept_id": cid})
    except Exception as e:
        RESUMEN["errores"].append(f"tag_concept {nid}: {e}")


# ── Collections ───────────────────────────────────────────────────────────────

_COLLECTIONS: dict[str, str] = {}
PENDIENTES_DOS_TEMAS: dict[frozenset, list] = {}
_COMPONENTES: dict[str, frozenset] = {}   # nombre normalizado -> temas que la componen
_HIBRIDOS: dict[frozenset, str] = {}      # conjunto de temas -> nombre del hibrido
MARCA = "[componentes: "


def _cargar_collections() -> list[dict]:
    """Lee las collections y recupera de que temas se compone cada hibrida.
    Los componentes viven en la descripcion, asi que el mismo par resuelve
    siempre a la misma collection aunque cambie la corrida."""
    try:
        d = api("GET", "/api/v1/collections")
        lista = d.get("collections", d) if isinstance(d, dict) else (d or [])
    except Exception as e:
        RESUMEN["errores"].append(f"list_collections: {e}")
        return []
    for c in lista:
        nombre = str(c.get("name", "")).strip()
        if not nombre:
            continue
        _COLLECTIONS[_norm(nombre)] = c["id"]
        desc = str(c.get("description") or "")
        if MARCA in desc:
            crudo = desc.split(MARCA, 1)[1].split("]", 1)[0]
            comps = frozenset(p.strip().lower() for p in crudo.split("|") if p.strip())
            if len(comps) > 1:
                _COMPONENTES[_norm(nombre)] = comps
                _HIBRIDOS[comps] = nombre
    return lista


def componentes_de(nombre: str) -> frozenset:
    """Un tema simple se compone de si mismo. Uno hibrido, de sus partes."""
    return _COMPONENTES.get(_norm(nombre), frozenset({_norm(nombre)}))


def nombrar_hibrido(temas: list[str]) -> str:
    """El nombre del tema emergente es juicio, no concatenacion."""
    try:
        resp = _llm(
            "Das nombre al tema que une varios temas. No es una suma ni una lista: es el "
            "campo que de verdad los contiene. 'fotografia' + 'viajes' -> 'fotografia de "
            "viaje'. Responde SOLO el nombre, en espanol, minusculas, de dos a cinco "
            "palabras, sin comillas ni puntuacion ni explicacion.",
            "Temas que convergen:\n" + "\n".join(f"- {t}" for t in temas), esfuerzo="low")
        limpio = _nombre_valido(resp, 70)
        if limpio:
            return limpio
    except Exception as e:
        RESUMEN["errores"].append(f"nombrar_hibrido {temas}: {e}")
    return " y ".join(sorted(temas))


def decidir_temas_emergentes(pares: dict) -> dict:
    """UNA llamada por corrida. Recibe todos los pares de temas que comparten
    notas y decide cuales son de verdad un tema mas amplio.

    POR QUE ASI Y NO COMO ANTES: decidirlo nota a nota, sobre la marcha, se
    desboca. En una corrida produjo once colecciones para unas pocas hipotesis, porque el
    hibrido de un paso entraba como componente del siguiente y cada encuentro
    pedia un nombre nuevo sin memoria de los anteriores. De ahi salieron tres
    nombres distintos para lo mismo.

    Viendo todos los pares de golpe, el juez puede decir que tres temas van a UNO
    solo, y nombrarlo una vez.

    Devuelve {frozenset(temas): nombre}.
    """
    if not pares:
        return {}
    lineas = []
    for temas, notas in pares.items():
        lineas.append(f"- {' + '.join(sorted(temas))}  ({len(notas)} nota(s) en comun)")
    sistema = (
        "Varios temas comparten material: hay notas que pertenecen a dos temas a la vez.\n\n"
        "Cuando dos temas convergen en uno mas amplio que de verdad existe, ese tema mas "
        "amplio se nombra y el material compartido vive alli. Ejemplo: \"fotografia\" y "
        "\"viajes\" convergen en \"fotografia de viaje\".\n\n"
        "Pero que dos temas compartan una nota NO significa que converjan. Casi siempre "
        "solo significa que esa nota habla de las dos cosas. Un tema mas amplio solo "
        "existe si tiene nombre propio y sentido para una persona.\n\n"
        "SESGO A NO CREAR. Cada tema que inventes es una carpeta mas que el usuario tiene "
        "que entender. Si dudas, no lo crees.\n\n"
        "Si varios grupos son en realidad el mismo tema amplio, dales el MISMO nombre.\n\n"
        "Responde SOLO un objeto JSON: clave = los temas unidos por ' + ' tal como te los "
        "doy, valor = el nombre del tema amplio, en espanol y minusculas. Los grupos que no "
        "convergen no aparecen. Si ninguno converge, responde {}.")
    try:
        data = _json_del_llm(_llm(sistema, "\n".join(lineas), esfuerzo="high")) or {}
    except Exception as e:
        RESUMEN["errores"].append(f"decidir_temas_emergentes: {e}")
        return {}
    salida = {}
    if isinstance(data, dict):
        for clave, nombre in data.items():
            partes = frozenset(_norm(p) for p in str(clave).split("+") if p.strip())
            limpio = _nombre_valido(nombre, 70)
            if partes in pares and limpio:
                salida[partes] = limpio
    return salida


def collection_hibrida(temas_norm: frozenset, etiquetas: list[str],
                       nombre_sugerido: str = "") -> tuple[str, str] | None:
    """Encuentra o crea la collection del tema emergente. Devuelve (nombre, id)."""
    if temas_norm in _HIBRIDOS:
        nombre = _HIBRIDOS[temas_norm]
        cid_cache = _COLLECTIONS.get(_norm(nombre))
        if cid_cache:
            return nombre, cid_cache
        # El cache dice que existe pero no esta en _COLLECTIONS: se cayo por
        # medio, o alguien lo sembro antes de crearla. Se sigue y se crea.
    nombre = nombre_sugerido or nombrar_hibrido(sorted(etiquetas))
    desc = ("Tema emergente: el material que pertenece a la vez a varios temas vive aqui, "
            "no repartido entre ellos. " + MARCA + "|".join(sorted(temas_norm)) + "]")
    try:
        d = api("POST", "/api/v1/collections", json={"name": nombre, "description": desc})
        cid = (d or {}).get("id")
    except Exception as e:
        if "409" in str(e):
            # Ya existe con ese nombre: se recupera, no se falla.
            _cargar_collections()
            cid = _COLLECTIONS.get(_norm(nombre))
            if not cid:
                return None
        else:
            RESUMEN["errores"].append(f"crear hibrida {nombre}: {e}")
            return None
    if not cid:
        return None
    _COLLECTIONS[_norm(nombre)] = cid
    _COMPONENTES[_norm(nombre)] = temas_norm
    _HIBRIDOS[temas_norm] = nombre
    RESUMEN["collections"].append({"nombre": nombre, "id": cid, "creada": True,
                                   "hibrida_de": sorted(etiquetas)})
    log(f"  tema emergente: {' + '.join(sorted(etiquetas))} -> \"{nombre}\"")
    return nombre, cid

def collection_id(nombre: str) -> str | None:
    clave = _norm(nombre)
    if clave in _COLLECTIONS:
        return _COLLECTIONS[clave]
    try:
        if not _COLLECTIONS:
            _cargar_collections()
        if clave in _COLLECTIONS:
            return _COLLECTIONS[clave]
        try:
            d = api("POST", "/api/v1/collections",
                    json={"name": nombre.strip(),
                          "description": "Collection emergente del debate. Agrupa el material del tema, no solo las hipotesis."})
            cid = (d or {}).get("id")
        except Exception as e:
            if "409" not in str(e):
                raise
            _COLLECTIONS.clear()
            _cargar_collections()
            return _COLLECTIONS.get(clave)
        if cid:
            _COLLECTIONS[clave] = cid
            RESUMEN["collections"].append({"nombre": nombre, "id": cid, "creada": True})
        return cid
    except Exception as e:
        RESUMEN["errores"].append(f"collection {nombre}: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# LECTURA DEL DEBATE
# ══════════════════════════════════════════════════════════════════════════════

def cargar_hipotesis(run_dir: Path) -> list[dict]:
    p = run_dir / "theory.json"
    for _ in (1,):
        if not p.exists():
            break
        d = json.loads(p.read_text(encoding="utf-8"))
        hs = d.get("hypotheses") if isinstance(d, dict) else d
        if isinstance(hs, list) and hs and isinstance(hs[0], dict) and "pattern" in hs[0]:
            log(f"leidas {len(hs)} hipotesis de {p.name}")
            return hs
        raise SystemExit(
            f"{p} existe pero no tiene hipotesis del debate: no trae 'pattern'.\n"
            "Casi seguro es la teoria de GTFlow (selective coding), que escribe con el "
            "mismo nombre. Ese es el organo que inventa personajes. El piloto nunca corre "
            "esa etapa; si este fichero es de GTFlow, alguien corrio 'gtflow run-all' a mano "
            "sobre este directorio y el theory.json del debate se perdio.")
    raise SystemExit(f"No existe {run_dir}/theory.json")


def tema_de(h: dict) -> str:
    """El debate escribe `asunto` (el titulo de la hipotesis, no la collection --
    ver e2_debate.py). Antes se llamaba `collection`, y las corridas viejas
    siguen usando ese nombre en su theory.json: se lee por compatibilidad.
    resolver_collections sobreescribe esto con el campo real antes de que se use
    para archivar nada; hasta entonces, esta funcion solo devuelve el asunto."""
    # ORDEN CRITICO: `_campo` primero. Es lo que decide resolver_collections, y
    # tiene que ganarle al `asunto` que escribio el agente. Antes `asunto` iba
    # primero, asi que tema_de() devolvia SIEMPRE la etiqueta del agente y la
    # decision del enrutado se guardaba en h["collection"] sin que nadie la
    # volviera a leer: el enrutado no se aplicaba nunca. El sintoma de un campo
    # por hipotesis es eso, no solo que el modelo copiara.
    for k in ("_campo", "asunto", "collection", "category"):
        v = str(h.get(k) or "").strip()
        if v:
            return v
    # Sin tema declarado: se deriva del pattern para no perder la hipotesis.
    palabras = re.sub(r"[^\wáéíóúñü ]", " ", str(h.get("pattern", "")), flags=re.I).split()
    return " ".join(palabras[:6]) or "sin-tema"


# ══════════════════════════════════════════════════════════════════════════════
# CAPA SEMI-DETERMINISTA:  candidatos deterministas -> juicio del LLM -> fusion
# ══════════════════════════════════════════════════════════════════════════════
# El parser no puede decidir solo si dos hipotesis son la misma: eso es juicio.
# Pero tampoco hace falta un debate entero para preguntarlo. Tres capas:
#   1. deteccion determinista de CANDIDATOS (note_ids compartidos, misma
#      collection, solape de vocabulario del mechanism). No decide, senala.
#   2. una llamada barata al LLM por grupo de candidatos, que responde SOLO
#      cuales se fusionan. El criterio es HOLISTICO: el juez mira
#      las piezas enteras y decide si describen el mismo hallazgo. NO se
#      reduce al falsador -- dos hipotesis pueden tener falsadores distintos
#      y ser el mismo fenomeno visto desde angulos distintos.
#   3. la fusion la ejecuta este codigo, deterministicamente.
# El debate ya puede senalar duplicados con duplicate_of; esto cubre lo que no
# senalo, que en una corrida puede ser todo.

LLM_URL = os.environ.get("TINFOIL_BASE_URL", "http://tinfoil-proxy:3302/v1")
LLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("N3_PARSER_MODEL", "gpt-oss-120b")


TOPE_TOKENS = 0        # 0 = sin tope. Lo pone --max-tokens-llm si alguna vez hace falta.


def _llm(system: str, user: str, esfuerzo: str = "high") -> str:
    """Sin max_tokens por defecto, a proposito.

    gpt-oss-120b razona, y el razonamiento SALE DEL MISMO PRESUPUESTO que la
    respuesta. Un tope de 30 tokens puesto porque "un nombre son tres palabras"
    hace que el modelo gaste el cupo razonando y devuelva content=null con
    finish_reason=length — HTTP 200, sin error, y aguas abajo aparece una
    coleccion llamada "None". El tope venia copiado del debate, donde tiene
    sentido porque son cientos de llamadas con techo de gasto. Aqui son unas
    pocas por corrida: no protege de nada y rompe.

    reasoning_effort va alto donde hay juicio (fusion, enrutado) y bajo donde
    solo hay que escribir un nombre. El proxy lo reenvia: esta en su KNOWN_CHAT.
    """
    payload = {"model": LLM_MODEL, "temperature": 0.1,
               "reasoning_effort": esfuerzo,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    if TOPE_TOKENS:
        payload["max_tokens"] = TOPE_TOKENS
    r = requests.post(f"{LLM_URL}/chat/completions", timeout=300,
                      headers={"Authorization": f"Bearer {LLM_KEY}",
                               "Content-Type": "application/json"},
                      json=payload)
    if r.status_code >= 400:
        raise RuntimeError(f"LLM {r.status_code}: {r.text[:200]}")
    choice = (r.json().get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    contenido = msg.get("content")
    if isinstance(contenido, str) and contenido.strip():
        return contenido
    # Algunos modelos dejan la salida fuera de content.
    for alt in ("reasoning_content", "reasoning", "text"):
        if isinstance(msg.get(alt), str) and msg[alt].strip():
            return msg[alt]
    fin = choice.get("finish_reason")
    if fin == "length":
        raise RuntimeError(
            f"El modelo se quedo sin presupuesto razonando y no llego a responder"
            + (f" (tope {TOPE_TOKENS})." if TOPE_TOKENS else " sin tope puesto: revisa el proxy."))
    raise RuntimeError(
        f"content vacio (finish_reason={fin}, campos={sorted(msg)}): {json.dumps(msg)[:200]}")


def _json_del_llm(texto: str) -> Any:
    texto = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.M).strip()
    m = re.search(r"[\[{].*[\]}]", texto, re.S)
    return json.loads(m.group(0)) if m else None


def _json_robusto(raw: str):
    """Como _json_del_llm pero NO asume `[{` adyacente.

    El regex con re.S funciona cuando el JSON
    esta en una sola linea, pero falla con JSON pretty-printed porque el `.*`
    devora todo hasta el ULTIMO `}`. El LLM de gpt-oss devuelve `[\n{`, con
    un salto de linea entre el `[` y el `{`, y eso ya rompe la busqueda de
    inicio adyacente. Fix: buscar el primer `[`/`{` y el ultimo
    `]`/`}` por separado; probar con y sin comas colgantes.
    """
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


_NO_NOMBRES = {"none", "null", "n/a", "na", "nan", "undefined", "sin nombre",
               "unknown", "desconocido", "crear", "true", "false"}


def _nombre_valido(resp: Any, tope: int) -> str | None:
    """Un nombre de collection que no lo sea es peor que no renombrar: se
    escribe en Fortemi y hay que borrarlo a mano."""
    limpio = " ".join(str(resp or "").strip().strip('"\'.').split())[:tope]
    if not (3 <= len(limpio) <= tope):
        return None
    if limpio.lower() in _NO_NOMBRES:
        return None
    return limpio


def _vocab(s: str) -> set:
    palabras = re.findall(r"\w{4,}", _sin_tildes(str(s)).lower())
    return {p for p in palabras if p not in _VACIAS}


_VACIAS = {"para", "porque", "cuando", "donde", "sobre", "entre", "autor", "esto",
           "esta", "este", "como", "mismo", "misma", "hace", "hacer", "tiene",
           "sino", "pero", "mas", "menos", "muy", "sus", "una", "unos", "unas",
           "self", "that", "with", "from", "this"}


def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def candidatos_de_fusion(hs: list[dict], min_solape: float) -> list[list[dict]]:
    """ARCHIVADA. No se usa por defecto (--por-candidatos la activa).

    Agrupaba candidatos por notas compartidas, misma collection y solape de
    vocabulario del mechanism. Ninguno de los tres es el criterio que decidimos,
    que es el falsador. Y peor: filtraba ANTES del juicio fino, asi que un par
    que se debia fusionar por identidad de falsador y no compartia ni notas ni
    collection no llegaba nunca a que lo juzgaran. Ademas "notas compartidas" es
    un criterio ya descartado explicitamente: una misma nota puede ser evidencia
    de varias hipotesis distintas.

    Se conserva para cuando el volumen crezca y una sola llamada no pueda
    compararlas bien. Ese dia esto es una optimizacion, no un criterio, y
    deberia agrupar por falsador.
    """
    import itertools
    pares = []
    for x, y in itertools.combinations(hs, 2):
        if x.get("duplicate_of") or y.get("duplicate_of"):
            continue
        vx, vy = _vocab(x.get("mechanism", "")), _vocab(y.get("mechanism", ""))
        if (set(x.get("evidence") or []) & set(y.get("evidence") or [])
                or tema_de(x).strip().lower() == tema_de(y).strip().lower()
                or (vx and vy and len(vx & vy) / len(vx | vy) >= min_solape)):
            pares.append((x, y))
    grupos: list[list[dict]] = []
    for x, y in pares:
        for g in grupos:
            if x in g or y in g:
                if x not in g: g.append(x)
                if y not in g: g.append(y)
                break
        else:
            grupos.append([x, y])
    return grupos


def juicio_de_fusion(hs: list[dict], mixto: bool = False) -> list[list[str]]:
    """Capa 2. UNA llamada con TODAS las hipotesis de la corrida delante.

    Sin pre-filtro. El criterio es holistico, asi que el juez tiene que poder
    comparar todas contra todas; cualquier capa que agrupe antes por otra cosa
    le esconde pares. Con 9-30 hipotesis por corrida cabe de sobra en el
    contexto, y es mas simple ademas de mas fiel.
    """
    if len(hs) < 2:
        return []
    sistema = (
        # Criterio HOLISTICO. El falsador era una propuesta anterior
        # que el usuario rechazó: dos hipotesis pueden tener falsadores distintos y ser
        # el mismo hallazgo, porque son angulos del mismo fenomeno.
        "Te dan todas las hipotesis que un debate produjo sobre un mismo periodo. "
        "Dices cuales son en realidad la misma hipotesis dicha de maneras distintas.\n\n"
        "MIRA TODAS LAS PIEZAS: patron, mecanismo, prediccion, lo que la tumbaria, "
        "historia y evidencia. NO decidas por un solo campo: dos hipotesis pueden "
        "hablar de lo mismo con mecanismos o falsadores distintos, porque son el "
        "mismo fenomeno visto desde angulos distintos.\n\n"
        "PUEDES AGRUPAR hipotesis de estados distintos (activa, falsada, sin_responder). "
        "El trazo de por que cada una cayo o sobrevivio se conserva.\n\n"
        "SESGO A NO FUSIONAR. Ante la duda, NO fusiones.\n\n"
        "Responde SOLO un array JSON de arrays de ids, por ejemplo "
        "[[\"H-002\",\"H-006\"]]. Cada array interior es un conjunto que se fusiona. "
        "Las que no se fusionan con nadie no aparecen. Si ninguna se fusiona, responde "
        "[]. Sin explicaciones, sin markdown.")
    partes = []
    for h in hs:
        resumen = " ".join(str(h.get("resumen") or "").split())
        partes.append(
            f"{h['id']}  [estado: {h.get('status','?')}]  [coleccion: {tema_de(h)}]\n"
            + (f"  en una linea: {resumen}\n" if resumen else "")
            + f"  patron:     {h.get('pattern','')}\n"
            f"  mecanismo:  {h.get('mechanism','')}\n"
            f"  prediccion: {h.get('prediction','')}\n"
            f"  FALSADOR:   {h.get('falsifier','')}")
    try:
        # esfuerzo="medium" (high corta el JSON en gpt-oss);
        # _json_robusto en vez de _json_del_llm (del_llm asume [{ adyacente)
        raw_juzgar = _llm(sistema, "\n\n".join(partes), esfuerzo="medium")
        data = _json_robusto(raw_juzgar) or []
        por_id = {h["id"]: h for h in hs}
        salida = []
        for conj in data:
            limpio = [i for i in conj if i in por_id]
            if len(limpio) < 2:
                continue
            estados = {por_id[i].get("status") for i in limpio}
            if len(estados) > 1 and not mixto:
                # Estatus mixto sigue sin decidirse. No se decide aqui: se
                # reporta para que lo mires tu.
                RESUMEN["fusiones_estatus_mixto"].append(
                    {"grupo": sorted(limpio), "estados": sorted(str(e) for e in estados)})
                continue
            salida.append(sorted(limpio))
        return salida
    except Exception as e:
        RESUMEN["errores"].append(f"juicio_de_fusion: {e}")
        return []


def nombrar_collection(nombre: str, hs: list[dict]) -> str:
    """Capa 2 bis. Si el nombre es un codigo tecnico o un trozo de pattern, se
    pide uno legible. El usuario va a ver estos nombres escritos."""
    crudo = ("_" in nombre or len(nombre.split()) > 5
             or nombre.strip().lower() != nombre.strip().lower().strip(".,;"))
    if not crudo:
        return nombre
    try:
        resp = _llm(
            "Das nombre a una coleccion tematica que un humano va a ver escrita. "
            "Responde SOLO el nombre, en espanol, de una a cuatro palabras, en "
            "minusculas, sin comillas ni puntuacion ni explicacion.",
            "Nombre actual (tecnico o mal formado): " + nombre + "\n\nHipotesis del tema:\n"
            + "\n".join(f"- {h.get('pattern','')[:220]}" for h in hs), esfuerzo="low")
        limpio = _nombre_valido(resp, 60)
        if limpio:
            RESUMEN["collections_renombradas"].append({"de": nombre, "a": limpio})
            return limpio
    except Exception as e:
        RESUMEN["errores"].append(f"nombrar_collection {nombre}: {e}")
    return nombre


# ══════════════════════════════════════════════════════════════════════════════
# FUSION DE DUPLICADOS (ejecucion — capa 3)
# ══════════════════════════════════════════════════════════════════════════════

def absorber(hs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aplica duplicate_of. La canonica se queda con el ID de la primera y
    concatena a las absorbidas en orden, cada bloque con el estatus que traia.

    Solo fusiona lo que el debate marco. La deteccion la hace el agente; la
    ejecucion, este codigo. Ni el agente fusiona ni este script decide.
    """
    por_id = {h["id"]: h for h in hs}
    absorbidas: list[dict] = []

    for h in hs:
        dup = h.get("duplicate_of")
        if not dup:
            continue
        canon = por_id.get(dup)
        if not canon or canon["id"] == h["id"]:
            continue
        # Encadenamiento: si la canonica tambien es duplicada, sube hasta la raiz.
        visto = {h["id"]}
        while canon.get("duplicate_of") and canon["duplicate_of"] not in visto:
            visto.add(canon["id"])
            canon = por_id.get(canon["duplicate_of"], canon)

        canon.setdefault("_absorbidas", []).append(h)
        for nid in h.get("evidence", []):
            if nid not in canon.setdefault("evidence", []):
                canon["evidence"].append(nid)
        for per, ids in (h.get("evidence_by_period") or {}).items():
            destino = canon.setdefault("evidence_by_period", {}).setdefault(per, [])
            for nid in ids:
                if nid not in destino:
                    destino.append(nid)
        canon.setdefault("history", []).extend(h.get("history", []))
        h["_absorbida_en"] = canon["id"]
        absorbidas.append(h)
        RESUMEN["absorbidas"].append({"id": h["id"], "en": canon["id"]})

    vivas = [h for h in hs if not h.get("_absorbida_en")]
    return vivas, absorbidas


# ══════════════════════════════════════════════════════════════════════════════
# CONSOLIDACION DE TEMAS
# ══════════════════════════════════════════════════════════════════════════════
# Los temas no se declaran: emergen. La primera corrida solo puede producir temas
# estrechos, porque es lo unico que hay delante — "orden de la rutina diaria"
# sale de una hipotesis, no de una vision del corpus. Eso no es un
# defecto que haya que evitar escribiendo: es el estado inicial.
#
# Lo que hacia falta no era una lista cerrada de macrotemas ni un sesgo mas duro
# al crear. Era esto: un paso que mira los temas que YA existen, reconoce cuales
# son el mismo, y los funde en el que los contiene. Ensanchar es una migracion, y
# migrar no pierde nada — la coleccion es la ubicacion vigente, revisable.
#
# Asi el macrotema aparece cuando hay material suficiente para verlo, no antes. Y
# la fragmentacion de una corrida deja de ser permanente, porque hay algo que la
# deshace.
#
# Borrar una coleccion no borra notas: delete_tx hace
# UPDATE note SET collection_id = NULL y luego DELETE FROM collection.
# Aqui ademas se mueven antes, asi que nunca queda una nota suelta.

def _contenido_de_collection(cid: str, cuantas: int = 4) -> tuple[int, list[str]]:
    """Devuelve (n_notas, muestras_de_contenido_real).

    OJO: para una nota de hipotesis, el titulo H1 es "# H-002 — <asunto>" — el
    mismo texto que ya es el nombre de la coleccion. Si la muestra fuera solo
    ese titulo, resolver_collections y consolidar_collections verian el nombre
    de la coleccion REPETIDO como si fuera su contenido, sin ninguna senal
    nueva. Asi es como una coleccion no se reconocio como parte de su campo: la
    unica muestra que veia el juez era su propio nombre.

    Por eso aqui se saca el PATRON real (la seccion ## Patron de la nota de
    hipotesis), que es donde esta el contenido de verdad. Para
    notas normales (no-hipotesis), se usa el cuerpo tal cual.
    """
    # /collections/{id}/notes NO tiene tope (el limit va directo al SQL, y acepta
    # offset). El tope de 100 es solo de GET /notes, que es otro endpoint. Aqui se
    # pide alto a proposito: si el conteo se quedara corto, el barrido creeria que
    # una collection tiene notas cuando ya esta vacia, o al reves.
    try:
        d = api("GET", f"/api/v1/collections/{cid}/notes", params={"limit": 2000})
        notas = d.get("notes", d) if isinstance(d, dict) else (d or [])
    except Exception:
        return 0, []
    muestras = []
    for n in notas:
        cuerpo = n.get("content") or ""
        titulo = (n.get("title") or "").strip()
        m_patron = re.search(r"^##\s*Patron\s*\n(.+?)(?:\n##|\Z)", cuerpo, re.S | re.M)
        if m_patron:
            etiqueta = " ".join(m_patron.group(1).split())[:160]
        else:
            m_h1 = re.search(r"^#\s*(.+)$", cuerpo, re.M)
            etiqueta = (m_h1.group(1).strip() if m_h1 else titulo)[:160]
        if etiqueta and len(muestras) < cuantas:
            muestras.append(etiqueta)
    return len(notas), muestras


def consolidar_collections(dry: bool) -> None:
    """Una llamada. Ve todos los temas que existen y dice cuales son el mismo."""
    _COLLECTIONS.clear(); _COMPONENTES.clear(); _HIBRIDOS.clear()
    lista = _cargar_collections()
    if len(lista) < 3:
        log(f"solo hay {len(lista)} tema(s). Nada que consolidar todavia.")
        return

    inventario, lineas = {}, []
    for c in lista:
        nombre = str(c.get("name", "")).strip()
        if not nombre:
            continue
        n, muestras = _contenido_de_collection(c["id"])
        inventario[_norm(nombre)] = {"id": c["id"], "nombre": nombre, "n": n}
        lineas.append(f'"{nombre}" ({n} nota(s))'
                      + (": " + "; ".join(muestras) if muestras else ""))

    sistema = (
        "Tienes las colecciones de un corpus personal con una muestra de lo que contiene "
        "cada una. Colapsa las sub-divisiones de un mismo campo al campo.\n\n"
        "CAMPO vs SUB-DIVISION. Un campo es amplio: \"fotografia\", \"viajes\", "
        "\"trabajo\". Una sub-division es estrecha: \"fotografia de calle\", "
        "\"fotografia de estudio\", \"edicion de imagenes\", \"archivo "
        "fotografico\". Las sub-divisiones NO son campos distintos: son temas dentro del "
        "mismo campo. Colapsar \"fotografia de calle\" + \"fotografia de "
        "estudio\" + \"edicion de imagenes\" al campo \"fotografia\" es correcto.\n\n"
        "COLECCION HOLISTICA. \"fotografia de viaje\" es la conjuncion de DOS "
        "CAMPOS GENUINAMENTE DISTINTOS (\"fotografia\" y \"viajes\"). Esta NO colapsa "
        "a ninguno de los dos — es su propio campo emergente.\n\n"
        "LA PREGUNTA NO ES '¿son el mismo tema?', sino '¿son sub-divisiones del mismo "
        "campo?'. Calle y estudio son temas distintos DENTRO de fotografia: por "
        "eso colapsan a fotografia, no porque sean lo mismo.\n\n"
        "SESGO A PRESERVAR. Un campo que ya tiene nombre propio y notas dentro NO se "
        "absorbe: es un contenedor util para navegar. Solo funde cuando dos nombres "
        "dicen LO MISMO con otras palabras (\"equipo y material\" y \"herramientas "
        "de trabajo\"), no cuando uno es una especializacion del otro (\"fotografia "
        "nocturna\" NO se funde en \"fotografia\": es mas preciso y por eso "
        "vale mas). "
        "SESGO A NO FUSIONAR cuando son campos genuinamente distintos.\n\n"
        "Responde SOLO un array JSON de objetos {\"temas\": [nombres exactos que te di], "
        "\"nombre\": \"el campo que los contiene\"}, en espanol y minusculas. Solo grupos "
        "de dos o mas. Si nada que consolidar, responde [].")

    try:
        data = _json_del_llm(_llm(sistema, "\n".join(lineas), esfuerzo="high")) or []
    except Exception as e:
        RESUMEN["errores"].append(f"consolidar: {e}")
        return
    if not isinstance(data, list):
        return

    for grupo in data:
        miembros = [inventario[_norm(x)] for x in (grupo.get("temas") or [])
                    if _norm(x) in inventario]
        nombre = _nombre_valido(grupo.get("nombre"), 70)
        if len(miembros) < 2 or not nombre:
            continue

        # El destino: uno de los miembros si el nombre coincide, o uno nuevo.
        destino = next((m for m in miembros if _norm(m["nombre"]) == _norm(nombre)), None)
        origenes = [m for m in miembros if not destino or m["id"] != destino["id"]]
        log(f'  "{nombre}"  <-  ' + ", ".join(f'"{m["nombre"]}"' for m in miembros))
        RESUMEN["consolidaciones"].append(
            {"nombre": nombre, "absorbe": [m["nombre"] for m in miembros],
             "notas": sum(m["n"] for m in miembros)})
        if dry:
            continue

        if destino is None:
            cid = collection_id(nombre)
            if not cid:
                continue
        else:
            cid = destino["id"]

        for m in origenes:
            try:
                # MISMO endpoint roto que _contenido_de_collection tenia:
                # GET /notes?collection_id= lo ignora Axum. Este era el segundo
                # sitio donde quedaba sin arreglar -- y es el que de verdad mueve
                # notas, no solo las muestrea. Con el filtro ignorado, esta
                # llamada devolvia lo que sea que el endpoint sin filtro
                # devuelve por defecto (parece ordenado por actividad reciente),
                # y aqui se movian ESAS notas -- no las de la collection que se
                # queria fundir. Como las notas de hipotesis acababan de
                # tocarse (SKOS adjuntado unos pasos antes en esta misma
                # corrida), eran las mas "recientes" del corpus entero, y el
                # grupo procesado AL FINAL se las llevaba todas -- por eso las
                # hipotesis terminaban ahi, y las notas citadas (mas viejas, no
                # tocadas hoy) se quedaban donde el paso 5 SI las habia puesto bien.
                d = api("GET", f"/api/v1/collections/{m['id']}/notes", params={"limit": 2000})
                notas = d.get("notes", d) if isinstance(d, dict) else (d or [])
            except Exception as e:
                RESUMEN["errores"].append(f"leer {m['nombre']}: {e}")
                continue
            movidas = 0
            for n in notas:
                try:
                    api("POST", f"/api/v1/notes/{n['id']}/move", json={"collection_id": cid})
                    movidas += 1
                except Exception as e:
                    RESUMEN["errores"].append(f"move {n.get('id')}: {e}")
            if movidas == len(notas):
                try:
                    api("DELETE", f"/api/v1/collections/{m['id']}")
                except Exception as e:
                    RESUMEN["errores"].append(f"borrar {m['nombre']}: {e}")
            else:
                RESUMEN["errores"].append(
                    f"{m['nombre']}: se movieron {movidas}/{len(notas)}. No se borra.")


# ══════════════════════════════════════════════════════════════════════════════
# A QUE COLLECTION VA CADA HIPOTESIS
# ══════════════════════════════════════════════════════════════════════════════
# El nombre que escribe el agente es una etiqueta, no la identidad del tema.
# Dos etiquetas pueden hablar del mismo tema sin compartir una palabra, y
# comparar los nombres entre si no decide nada: son palabras sin contexto.
#
# Lo que si tiene informacion es el texto de la hipotesis, y lo que identifica a
# una collection es el material que ya contiene. Asi que:
#
#   1. se busca el pattern de la hipotesis nueva contra las notas con tag
#      thesis que ya estan escritas (busqueda hibrida, sin LLM)
#   2. las collections donde viven esos resultados son las candidatas
#   3. UNA llamada por corrida decide, viendo todas las hipotesis a la vez —
#      asi tambien pilla que dos de la misma corrida propusieron sinonimos
#   4. este codigo aplica lo que decidio
#
# El sesgo del juez es CREAR. Una collection duplicada se ve, molesta y se
# arregla; una nota archivada bajo el tema equivocado no se ve nunca.
#
# En la primera corrida no hay nada escrito, no hay candidatas y se crea todo.
# Es el arranque, no hay vuelta.
#
# NO renombra collections existentes: si el nombre nuevo fuera mejor que el
# viejo, eso es otra potestad y no esta decidida.

def collection_de_nota(nid: str) -> str | None:
    n = get_note(nid)
    cid = (n or {}).get("collection_id")
    if not cid:
        return None
    for nombre, ident in _COLLECTIONS.items():
        if ident == cid:
            return nombre
    return None


def resolver_collections(hs: list[dict]) -> None:
    """UNA llamada, con TODAS las hipotesis de la corrida y TODOS los temas que ya
    existen delante. Fija h['collection'].

    POR QUE ASI. La version anterior preguntaba hipotesis por hipotesis contra
    unas pocas candidatas sacadas de una busqueda. Con esa vista el tema no se
    puede ver: cada hipotesis, mirada sola, produce una etiqueta con forma de
    hipotesis, y salen varias carpetas donde hay una. El material para ver que
    todas las hipotesis de una corrida son el mismo campo estaba entero en esa
    corrida — no hacia falta esperar al periodo siguiente ni escribir varios
    temas para fundirlos despues.

    Es el mismo error que ya cometi con el merge (pre-filtrar antes de juzgar) y
    con los temas emergentes (decidir nota a nota). El juez tiene que ver el
    conjunto.

    La etiqueta que escribio el agente NO es el tema: es el asunto de su
    hipotesis. Entra como pista, y acaba en el titulo de la nota.
    """
    if not hs:
        return
    if not _COLLECTIONS:
        _cargar_collections()

    existentes = []
    for nombre, cid in sorted(_COLLECTIONS.items()):
        n, muestras = _contenido_de_collection(cid, 2)
        real = next((k for k in _COLLECTIONS if k == nombre), nombre)
        existentes.append(f'"{real}" ({n} nota(s))' + (": " + "; ".join(muestras) if muestras else ""))

    lineas = []
    for h in hs:
        lineas.append(
            f"{h['id']}\n  hipotesis: {h.get('pattern','')}\n"
            f"  mecanismo: {h.get('mechanism','')[:300]}\n"
            f"  asunto que le puso el agente: \"{tema_de(h)}\"")

    sistema = (
        "Asignas cada hipotesis al CAMPO al que pertenece. Una coleccion es un campo amplio "
        "de experiencia o conocimiento: \"fotografia\", \"viajes\", \"trabajo\", "
        "\"idiomas\", \"lectura\". Es la primera pregunta que uno se hace ante un "
        "documento: ¿esto es de que?\n\n"
        "NO es el titulo de una hipotesis ni la descripcion de su contenido. "
        "\"orden de la rutina diaria\" no es un campo — es el titulo de una "
        "hipotesis. Su campo es \"rutinas\".\n\n"
        "SUB-DIVISIONES NO SON CAMPOS. \"calle\", \"estudio\", \"edicion\", "
        "\"archivo\", \"revelado\" son temas DENTRO del campo "
        "\"fotografia\", no campos distintos.\n\n"
        "ALGUNOS DE LOS TEMAS QUE YA EXISTEN NO SON CAMPOS. Si ves uno que suena a "
        "titulo de una hipotesis concreta —una frase que nombra un hallazgo, no un "
        "area de experiencia— "
        "eso es una hipotesis, no un campo, y suele tener una o dos notas dentro. "
        "NO lo reuses: asigna esas hipotesis al campo que de verdad les toca. Un "
        "campo agrupa muchas hipotesis distintas bajo una misma area; un titulo "
        "describe una sola.\n\n"
        "PERO NO TODO ES EL MISMO CAMPO. Que el autor sea una persona "
        "pensando no convierte cualquier tema en el mismo campo. Una hipotesis sobre "
        "como se percibe una imagen es TEORIA DE LA FOTOGRAFIA. Una sobre planificar "
        "un itinerario es LOGISTICA DE VIAJE. Una sobre reaprovechar material "
        "desechado es PRACTICA MATERIAL o TECNICA. Meterlas todas en un solo campo "
        "hace que la coleccion deje de discriminar nada: con decenas de notas dentro ya no "
        "sirve para navegar.\n\n"
        "Asigna campos DISTINTOS cuando el material es genuinamente distinto. No "
        "fuerces un solo campo para todo el conjunto.\n\n"
        "COLECCION HOLISTICA: si el material cruza genuinamente DOS CAMPOS DISTINTOS y "
        "esa conjuncion tiene nombre propio, nombrala: \"fotografia\" + \"viajes\" "
        "= \"fotografia de viaje\". Esto solo emerge cuando hay material de dos "
        "campos distintos de verdad.\n\n"
        "El asunto que te doy de cada hipotesis es una pista para enrutar, no el nombre "
        "del campo. No lo copies.\n\n"
        "Mira los campos que ya existen y reusalos si sirven. Crea los menos posibles.\n\n"
        "Responde SOLO un objeto JSON: clave = id de la hipotesis, valor = el campo, "
        "en espanol y minusculas. Todas las hipotesis tienen que aparecer.")
    usuario = ("HIPOTESIS DE ESTA CORRIDA\n" + "\n\n".join(lineas)
               + "\n\nTEMAS QUE YA EXISTEN\n"
               + ("\n".join(existentes) if existentes else "(ninguno todavia)"))

    def _pedir(extra: str = "") -> dict:
        try:
            d = _json_del_llm(_llm(sistema + extra, usuario, esfuerzo="high"))
        except Exception as e:
            RESUMEN["errores"].append(f"resolver_collections: {e}")
            return {}
        return d if isinstance(d, dict) else {}

    data = _pedir()
    if not isinstance(data, dict):
        return

    # CINTURON: si el LLM devuelve como "campo" la misma etiqueta que escribio el
    # agente, no ha decidido nada -- ha copiado. Eso es exactamente lo que paso en
    # una corrida: un campo por hipotesis, y todos eran los asuntos tal cual.
    # Se mide y, si copio la mayoria, se reintenta UNA vez diciendoselo.
    # El cinturon detecta UN fallo concreto: que el modelo copie el asunto de
    # cada hipotesis como si fuera su campo. NO castiga la diversidad de campos
    # por si misma: un periodo puede tocar fotografia, viajes y
    # practica material a la vez, y eso es correcto. Antes se disparaba con
    # `distintos > max(3, len(hs)//3)` y le decia al modelo "elige el mas amplio",
    # empujando todo a un solo campo -- que acabo con casi todas las notas dentro
    # y dejo de servir para navegar.
    copiados = sum(1 for h in hs
                   if _norm(str(data.get(h["id"], ""))) == _norm(tema_de(h)))
    distintos = len({_norm(str(v)) for v in data.values() if str(v).strip()})
    if hs and copiados > len(hs) // 2:
        log(f"  aviso: {copiados} de {len(hs)} campos son el asunto copiado tal cual. "
            f"Reintentando una vez.")
        data = _pedir(
            "\n\nATENCION: en tu intento anterior copiaste la etiqueta que traia cada "
            "hipotesis como si fuera su campo. Eso esta MAL: el asunto es el titulo de "
            "la hipotesis, no el campo al que pertenece. Decide el CAMPO de cada una "
            "(fotografia, viajes, trabajo, practica material, teoria del color...). "
            "Varias pueden compartir campo, y campos distintos son validos "
            "si el material es distinto.") or data
        nuevos_distintos = len({_norm(str(v)) for v in data.values() if str(v).strip()})
        log(f"  tras el reintento: {nuevos_distintos} campo(s)")
    if not isinstance(data, dict):
        return

    for h in hs:
        v = _nombre_valido(data.get(h["id"]), 70)
        if not v:
            continue
        antes = tema_de(h)
        if _norm(v) == _norm(antes):
            continue
        h["_asunto"] = antes          # se conserva: va al titulo de la nota
        h["_campo"] = v               # lo que tema_de() debe devolver a partir de ahora
        h["collection"] = v
        h["_collection_resuelta"] = True
        RESUMEN["collections_resueltas"].append(
            {"id": h["id"], "asunto": antes, "tema": v,
             "existente": _norm(v) in _COLLECTIONS})
    temas = sorted({tema_de(h) for h in hs})
    log(f"temas de la corrida: {len(temas)} para {len(hs)} hipotesis -> "
        + ", ".join(f'"{x}"' for x in temas))


def aplicar_emit_collection(run_dir: Path, escribibles: list[dict], args) -> None:
    """Lee collections.json -- la señal de emit_collection -- y mueve las notas
    que el agente marco como pertenecientes a un tema, aunque ninguna hipotesis
    las citara.

    ESTO NO EXISTIA. emit_collection escribe en collections.json y NINGUN paso
    del pipeline lo leia: ni el parser, ni el reparador de corridas. La unica
    señal que dijimos que solo el agente puede dar (que notas van con un tema
    sin ser evidencia de nada) se quedaba escrita en disco sin efecto. En una
    corrida real el agente_a la uso una vez y esa senal nunca llego a
    materializarse.

    Cada entrada de collections.json trae "notes": una lista MEZCLADA de note_ids
    y de H-NNN (asi la escribe emit_collection en el debate). Aqui se separan:
    los H-NNN dicen A QUE COLLECTION va (la que resolvio esa hipotesis en esta
    corrida); los note_ids son lo que hay que mover ahi.
    """
    p = run_dir / "collections.json"
    if not p.exists():
        return
    try:
        entradas = (json.loads(p.read_text(encoding="utf-8")) or {}).get("collections") or []
    except Exception as e:
        RESUMEN["errores"].append(f"collections.json: {e}")
        return
    if not entradas:
        return

    por_id_local = {h["id"]: h for h in escribibles}
    log(f"emit_collection: {len(entradas)} señal(es) en collections.json")

    for entrada in entradas:
        nombre_senal = str(entrada.get("name") or "").strip()
        elementos = entrada.get("notes") or []
        h_ids = [x for x in elementos if re.fullmatch(r"H-\d{3,}", str(x).upper())]
        note_ids = [x for x in elementos if UUID_RE.fullmatch(str(x))]

        # A que collection va: la de las hipotesis referenciadas, si escribieron.
        destinos = []
        for hid in h_ids:
            h = por_id_local.get(hid.upper()) or por_id_local.get(hid)
            if h and h.get("_col_id"):
                destinos.append((tema_de(h), h["_col_id"]))
        if destinos:
            from collections import Counter
            tema_final, col_final = Counter(destinos).most_common(1)[0][0]
            if len(set(d[1] for d in destinos)) > 1:
                log(f"  aviso: \"{nombre_senal}\" cita hipotesis en collections distintas "
                    f"({sorted(set(t for t,_ in destinos))}). Uso la mas repetida: \"{tema_final}\".")
        elif nombre_senal:
            # Sin hipotesis vivas referenciadas (o ninguna se escribio): la señal
            # vale por si misma, se busca/crea por su propio nombre.
            if args.dry_run:
                log(f"  [dry] \"{nombre_senal}\" sin hipotesis escritas -> crearia/usaria "
                    f"collection propia con {len(note_ids)} nota(s)")
                continue
            col_final = collection_id(nombre_senal)
            tema_final = nombre_senal
            if not col_final:
                RESUMEN["errores"].append(f"emit_collection \"{nombre_senal}\": sin destino")
                continue
        else:
            continue

        if not note_ids:
            continue
        if args.dry_run:
            log(f"  [dry] \"{nombre_senal}\" -> {len(note_ids)} nota(s) sin ser evidencia -> "
                f"\"{tema_final}\"")
            RESUMEN["emit_collection"].append(
                {"señal": nombre_senal, "tema": tema_final, "notas": len(note_ids), "dry": True})
            continue
        if args.sin_mover_notas:
            continue

        movidas = 0
        for nid in note_ids:
            try:
                api("POST", f"/api/v1/notes/{nid}/move", json={"collection_id": col_final})
                movidas += 1
            except Exception as e:
                RESUMEN["errores"].append(f"emit_collection move {nid}: {e}")
        RESUMEN["emit_collection"].append(
            {"señal": nombre_senal, "tema": tema_final, "notas": movidas})
        log(f"  \"{nombre_senal}\": {movidas} nota(s) (sin ser evidencia) -> \"{tema_final}\"")


def redactar_actualizacion(h: dict, periodo: str, args, tag_h: str = "") -> str:
    """Lo que ESTE periodo le anadio a una hipotesis que ya tiene nota.

    Solo el delta. El patron, el mecanismo, la prediccion y el falsador ya estan
    escritos en el primer bloque de la nota: repetirlos entero en cada periodo
    duplica el texto y hace la nota ilegible. En los bloques de periodos
    anteriores se veian los cuatro campos identicos a los del primero y
    "Evidencia (0 notas)" debajo.

    Aqui va solo lo que cambio: el estado nuevo, el ataque o la defensa de este
    periodo, la evidencia que se sumo, y los campos que un refine haya cambiado
    -- esos si se muestran, porque son distintos de los de arriba.
    """
    eventos = [e for e in (h.get("history") or [])
               if str(e.get("period") or "") == str(periodo)]
    estado = h.get("status", "?")
    L = [f"## {periodo}", ""]
    L.append(f"**Estado tras este periodo:** {estado}")
    probados = h.get("periods_tested") or []
    if probados:
        L.append(f"**Probada en:** {', '.join(str(x) for x in probados)}")
    L.append("")

    # El debate la propuso como nueva pero es esta misma hipotesis dicha con
    # otras palabras: el parser lo detecto y la esta alimentando en su lugar.
    # A diferencia de una heredada normal, esta NUNCA se escribio en ningun
    # lado -- no hay "primer bloque" con su patron. Va completo, no solo el
    # delta, o se pierde el hallazgo entero.
    origen = h.get("_absorber_en_heredada")
    if origen:
        L.append(f"### Otro angulo, detectado por el parser (el debate la propuso "
                 f"como {origen} nueva, es la misma hipotesis)")
        for campo, titulo in (("pattern", "Patron"), ("mechanism", "Mecanismo"),
                              ("prediction", "Prediccion"), ("falsifier", "Falsador")):
            if h.get(campo):
                L.append(f"**{titulo}:** {h[campo]}")
        L.append("")

    # Un refine cambia los campos: entonces SI hay que escribir los nuevos.
    refinada = [e for e in eventos if e.get("event") in ("refined", "refine")]
    if refinada:
        L.append("### Refinada en este periodo")
        for campo, titulo in (("pattern", "Patron"), ("mechanism", "Mecanismo"),
                              ("prediction", "Prediccion"), ("falsifier", "Falsador")):
            if h.get(campo):
                L += [f"**{titulo} (nueva version):** {h[campo]}"]
        L.append("")

    if h.get("challenge_reason") and any(e.get("event") == "challenged" for e in eventos):
        L += ["### Ataque recibido", h["challenge_reason"], ""]
    if h.get("defense") and any(e.get("event") == "defended" for e in eventos):
        L += ["### Defensa", h["defense"], ""]
    if h.get("rebate_falsifier") and any(e.get("event") == "defended" for e in eventos):
        L += ["### Rebate del falsador", h["rebate_falsifier"], ""]
    for e in eventos:
        if e.get("event") == "reinforced" and e.get("note"):
            L += ["### Reforzada", e["note"], ""]
        if e.get("event") in ("conceded", "falsado_por_defecto"):
            L += ["### Cayo aqui", quien_la_tumbo(h), ""]

    nuevas_ev = (h.get("evidence_by_period") or {}).get(str(periodo)) or []
    if nuevas_ev:
        L += [f"### Evidencia sumada en {periodo} ({len(nuevas_ev)} notas)",
              texto_notas(nuevas_ev, args.max_notas, args.max_chars_nota), ""]
    ce = h.get("counter_evidence") or []
    if ce and any(e.get("event") == "challenged" for e in eventos):
        L += [f"### Contra-evidencia de {periodo} ({len(ce)} notas)",
              texto_notas(ce, args.max_notas, args.max_chars_nota), ""]

    if eventos:
        L.append("### Lo que paso")
        for e in eventos:
            L.append(f"- `{e.get('ts','')}` **{e.get('event','')}** por {e.get('by','?')}"
                     + (f" — {str(e['reason'])[:300]}" if e.get("reason") else ""))
        L.append("")
    if len(L) <= 5:
        L.append("_(sin cambios registrados en este periodo)_")
    # Un solo punto de salida para traducir las referencias: los agentes
    # mencionan IDs locales del debate en el ataque, la defensa y la
    # historia, y aqui se convierten al numero con el que cada una se
    # publica en Fortemi.
    # El tag propio se protege para que no se retraduzca. Al alimentar, el id de
    # la hipotesis YA es su numero de Fortemi, asi que sirve de respaldo.
    return reescribir_referencias("\n".join(L).rstrip(),
                                  tag_h or str(h.get("id", "")).strip().upper())


# ══════════════════════════════════════════════════════════════════════════════
# REDACCION DE LA NOTA
# ══════════════════════════════════════════════════════════════════════════════

def quien_la_tumbo(h: dict) -> str:
    """El trazo importa: no pesa igual que un agente conceda en una ronda a que
    la tumbe N4. El estatus es el mismo; la procedencia se escribe."""
    for ev in reversed(h.get("history") or []):
        if ev.get("event") in ("conceded", "falsado_por_defecto"):
            return (f"Cayo por {ev.get('event')} el {ev.get('ts','?')}, "
                    f"a manos de {ev.get('by') or h.get('challenged_by') or '?'} "
                    f"(debate automatico, periodo {ev.get('period', h.get('origin_period','?'))}). "
                    f"Motivo: {ev.get('reason','sin motivo registrado')}")
    return "Cayo al cerrar el debate sin respuesta al falsador invocado."


def texto_notas(ids: list[dict | str], maximo: int, chars: int) -> str:
    """Trae el texto literal de cada nota citada. Sin esto N4 tiene ids y nada mas."""
    partes = []
    for nid in list(ids)[:maximo]:
        n = get_note(nid)
        if not n:
            partes.append(f"- `{nid}` — (no se pudo leer)")
            continue
        cuerpo = " ".join((n.get("content") or "").split())
        fecha = (n.get("created_at_utc") or "")[:10]
        # El titulo vive en note.title (raiz), no en metadata. Verificado contra
        # la respuesta documentada de GET /api/v1/notes/{id} y contra el codigo.
        titulo = (n.get("title") or (n.get("metadata") or {}).get("title") or "").strip()
        # El id plano primero, siempre, como ancla. Al lado el titulo enlazado.
        # [[Titulo]] es el enlace nativo de Fortemi, y su resolvedor busca por
        # TITULO (hit_title.to_lowercase() == title.to_lowercase()): [[uuid]] no
        # resolveria nunca. Muchas notas tienen title NULL, asi que el id es lo
        # unico que no falla.
        cab = f"- `{nid}`" + (f" — [[{titulo}]]" if titulo else "") + (f" — {fecha}" if fecha else "")
        partes.append(f"{cab}\n  > {cuerpo[:chars]}{'…' if len(cuerpo) > chars else ''}")
    return "\n".join(partes)


# ID local del debate -> tag con el que se publica en Fortemi. Se llena antes
# de escribir la primera nota y lo usa reescribir_referencias().
# Donde viven los parser_resumen.json de las corridas anteriores, para resolver
# las referencias a hipotesis heredadas.
CORPUS = Path(os.environ.get("N3_CORPUS", "/app/corpus"))

REFERENCIAS: dict[str, str] = {}
_REF_RE = re.compile(r"\b([Hh]-\d{3,})\b")


def reescribir_referencias(texto: str, propio: str = "") -> str:
    """Cambia las menciones a IDs locales del debate por el tag de Fortemi.

    El agente escribe "eso es H-044, no anclaje epistemico" usando el ID local
    de SU corrida. Si esa hipotesis se publica como H-029, la frase queda
    apuntando a un numero que en Fortemi es otra cosa. Aqui se traduce.

    Solo toca los IDs que estan en el mapa de esta corrida. Una referencia a una
    hipotesis heredada ya viene con su numero de Fortemi y se deja igual; una
    que no se reconoce se marca, para que quien lea sepa que ese numero no
    apunta a nada en vez de buscarlo en vano.
    """
    if not REFERENCIAS or not texto:
        return texto

    # El H1 y la cabecera ya llevan el numero FINAL, puesto por escribir(). Si se
    # traducen otra vez, el mapa los manda a donde apunte ese numero como si
    # fuera un ID local -- y la nota acaba titulada con un numero distinto del
    # que se le tagea. Se salta la primera linea y se protege el propio numero.
    lineas = texto.split("\n")
    cabecera, resto = (lineas[0], "\n".join(lineas[1:])) if lineas else ("", texto)
    propio = str(propio or "").upper()

    def _cambia(m):
        viejo = m.group(1).upper()
        if propio and viejo == propio:
            return m.group(1)
        nuevo = REFERENCIAS.get(viejo)
        if not nuevo:
            return m.group(1)
        if nuevo.upper() == viejo:
            return m.group(1)
        return nuevo

    return cabecera + ("\n" + _REF_RE.sub(_cambia, resto) if lineas else "")


def _ts_propuesta(h: dict) -> str:
    """Cuando se propuso esta hipotesis, para usar de timestamp de adherencia
    al concatenar. Del primer 'proposed' en su historia; si no hay (una nota
    vieja sin ese formato), la fecha de hoy."""
    for e in (h.get("history") or []):
        if e.get("event") == "proposed" and e.get("ts"):
            try:
                return str(e["ts"])[:16].replace("T", " ")
            except Exception:
                pass
    return _cc.ahora()


def redactar(h: dict, periodo: str, args, tag_h: str) -> str:
    # tag_h es el que de verdad se escribe en Fortemi (viene de `numero`, el
    # contador secuencial). h["id"] es el id LOCAL que le puso el debate a esta
    # corrida, y con absorciones o heredadas de por medio los dos numeros
    # divergen: una hipotesis local "H-021" puede acabar tageada "h-020" en
    # Fortemi porque delante hubo una absorbida que no consumio numero. Titular
    # la nota con el id local y taguearla con el otro produjo notas donde el
    # titulo dice H-021 y el tag dice h-020 -- la propia nota se contradice.
    # El titulo tiene que ser el mismo numero que el tag, siempre.
    hid = tag_h
    estado = h.get("status", "?")
    L = [f"# {hid} — {h.get('_asunto') or h.get('asunto') or tema_de(h)}", ""]
    L += [f"**Estado:** {estado}   ·   **Periodo de origen:** {h.get('origin_period', periodo)}"
          f"   ·   **Propuesta por:** {h.get('proposed_by','?')}", ""]
    if str(h.get("resumen") or "").strip():
        # El meollo en una linea, escrito por quien la propuso. Es lo que se lee
        # primero, y lo que los periodos siguientes ven en su indice sin cargar
        # la hipotesis entera.
        L += [f"> {' '.join(str(h['resumen']).split())}", ""]
    # FUSION = CONCATENACION, no volcado. Si esta hipotesis absorbio otras por
    # duplicate_of, sus cuatro campos NO se anotan en un bloque aparte con su
    # propio id (que ademas seria el id LOCAL, la misma fuente del bug de
    # arriba): se concatenan dentro de los campos de la canonica, cada
    # aportacion detras de su timestamp de propuesta como vineta. Asi la nota
    # final tiene UN patron, UN mecanismo, etc., con todas las voces dentro --
    # no una hipotesis con anexos.
    _pattern, _mechanism, _prediction, _falsifier = (
        h.get("pattern", ""), h.get("mechanism", ""),
        h.get("prediction", ""), h.get("falsifier", ""))
    for _abs in h.get("_absorbidas", []):
        _ts_c, _ts_a = _ts_propuesta(h), _ts_propuesta(_abs)
        if _abs.get("pattern"):
            _pattern = _cc.fundir_campo(_pattern, _ts_c, _abs["pattern"], _ts_a)
        if _abs.get("mechanism"):
            _mechanism = _cc.fundir_campo(_mechanism, _ts_c, _abs["mechanism"], _ts_a)
        if _abs.get("prediction"):
            _prediction = _cc.fundir_campo(_prediction, _ts_c, _abs["prediction"], _ts_a)
        if _abs.get("falsifier"):
            _falsifier = _cc.fundir_campo(_falsifier, _ts_c, _abs["falsifier"], _ts_a)
    L += ["## Patron", _pattern, "",
          "## Mecanismo", _mechanism, "",
          "## Prediccion", _prediction, "",
          "## Falsador", _falsifier, ""]

    if estado == "falsado":
        L += ["## Como cayo", quien_la_tumbo(h), ""]
        L += ["> Falsada no es borrada. Queda como resultado y es reversible: si material",
              "> posterior la sostiene, se reabre desde aqui con su historia intacta.", ""]
    if estado == "sin_responder":
        L += ["## Sin resolver",
              f"Fue atacada y el debate cerro antes de la respuesta. "
              f"Desafios registrados: {sum(1 for e in h.get('history',[]) if e.get('event')=='challenged')}, "
              f"defensas: {sum(1 for e in h.get('history',[]) if e.get('event')=='defended')}. "
              "Pendiente de N4.", ""]

    if h.get("challenge_reason"):
        L += ["## Ataque recibido", h["challenge_reason"], ""]
    if h.get("defense"):
        L += ["## Defensa", h["defense"], ""]
    if h.get("rebate_falsifier"):
        L += ["## Rebate del falsador", h["rebate_falsifier"], ""]

    ev = h.get("evidence") or []
    L += [f"## Evidencia ({len(ev)} notas)", texto_notas(ev, args.max_notas, args.max_chars_nota), ""]
    ce = h.get("counter_evidence") or []
    if ce:
        L += [f"## Contra-evidencia ({len(ce)} notas)",
              texto_notas(ce, args.max_notas, args.max_chars_nota), ""]

    ebp = h.get("evidence_by_period") or {}
    if len(ebp) > 1:
        L += ["## Evidencia por periodo"] + [f"- {p}: {len(v)} notas" for p, v in sorted(ebp.items())] + [""]



    hist = h.get("history") or []
    if hist:
        L += ["## Historia"]
        for e in hist:
            L.append(f"- `{e.get('ts','')}` **{e.get('event','')}** por {e.get('by','?')}"
                     + (f" — {e['reason']}" if e.get("reason") else ""))
        L.append("")

    L += ["---", f"Generado por e2_parser desde el debate del periodo {periodo}."]
    # Un solo punto de salida para traducir las referencias: los agentes
    # mencionan IDs locales del debate en el ataque, la defensa y la
    # historia, y aqui se convierten al numero con el que cada una se
    # publica en Fortemi.
    return reescribir_referencias("\n".join(L))


# ══════════════════════════════════════════════════════════════════════════════
# ESCRITURA
# ══════════════════════════════════════════════════════════════════════════════

def siguiente_numero() -> int:
    r"""Continua la numeracion H-NNN mirando los tags de Fortemi.

    OJO CON LAS MAYUSCULAS. Fortemi devuelve los tags en MINUSCULA: se escribe
    "H-001" y se lee "h-001". La version anterior comparaba con
    re.fullmatch(r"H-(\d{3,})", ...) -- sensible a mayusculas -- asi que no
    reconocia NINGUN tag existente y devolvia siempre 1. Por eso el log decia
    "la numeracion arranca en H-001" teniendo ya varias escritas, y por eso
    acabaron con numeros duplicados.
    """
    mayor = 0
    for n in notas_por_tag("thesis"):
        for t in (n.get("tags") or []):
            nombre = t.get("name", t) if isinstance(t, dict) else t
            m = re.fullmatch(r"[Hh]-(\d{3,})", str(nombre).strip())
            if m:
                mayor = max(mayor, int(m.group(1)))
    return mayor + 1


def notas_por_hnnn() -> dict[str, str]:
    """H-NNN (en mayuscula) -> note_id de la nota que ya lo tiene en Fortemi.

    Es el mapeo DETERMINISTA que sustituye a buscar la nota por parecido
    semantico. Una hipotesis heredada sabe cual es su nota: la que lleva su tag.
    No hay que adivinarlo.
    """
    mapa: dict[str, str] = {}
    for n in notas_por_tag("thesis"):
        for t in (n.get("tags") or []):
            nombre = t.get("name", t) if isinstance(t, dict) else t
            if re.fullmatch(r"[Hh]-\d{3,}", str(nombre).strip()):
                mapa[str(nombre).strip().upper()] = n.get("id")
    return mapa


def detectar_confusion_con_heredadas(nuevas: list[dict], heredadas: list[dict]) -> dict:
    """El debate propuso algo COMO NUEVO que en realidad es una heredada dicha
    otra vez. Deberia haber reforzado, no propuesto -- pero se confundio, y esto
    lo repara.

    Es distinto del merge intra-corrida (que compara nuevas entre si) y del
    destino_de determinista (que solo mira el tag): aqui SI hace falta juicio,
    porque la coincidencia no esta en ningun tag -- esta en que dos hallazgos
    son el mismo dicho con otras palabras.

    Criterio holistico: mira las 4 piezas enteras. Mismo sesgo: ante la
    duda, NO fundir -- una hipotesis nueva de mas se ve y se arregla despues; una
    fundida por error dentro de una heredada que no le corresponde se pierde
    dentro de otra voz y no hay forma facil de notar que paso.

    Devuelve {id_local_de_la_nueva: id_local_de_la_heredada}.
    """
    if not nuevas or not heredadas:
        return {}
    # Criterio HOLISTICO, igual que juicio_de_fusion.
    sistema = (
        "El debate de este periodo propuso estas hipotesis como NUEVAS. Puede que "
        "el agente se haya confundido y una de ellas sea en realidad la MISMA "
        "hipotesis que una de periodos anteriores, dicha con otras palabras -- en "
        "vez de reforzar la anterior, la propuso de cero.\n\n"
        "MIRA TODAS LAS PIEZAS de cada una: patron, mecanismo, prediccion, lo que "
        "la tumbaria. Son la misma cuando leidas enteras describen el mismo hallazgo, "
        "aunque tengan falsadores distintos o el mecanismo este dicho diferente.\n\n"
        "SESGO A NO FUNDIR. Una hipotesis nueva de mas se ve y se arregla luego; "
        "fundirla donde no corresponde la entierra dentro de otra voz. Ante la "
        "duda, dejala como nueva.\n\n"
        "Responde SOLO un objeto JSON {id_de_la_nueva: id_de_la_heredada}. Las que "
        "de verdad sean nuevas no aparecen. Si ninguna coincide, responde {}.")
    def _entrada(prefijo, h):
        return (f"{prefijo} {h['id']}\n"
                f"  patron:     {h.get('pattern','')}\n"
                f"  mecanismo:  {h.get('mechanism','')}\n"
                f"  prediccion: {h.get('prediction','')}\n"
                f"  lo que la tumba: {h.get('falsifier','')}")
    lineas_n = [_entrada("NUEVA", h) for h in nuevas]
    lineas_h = [_entrada("HEREDADA", h) for h in heredadas]
    usuario = "\n\n".join(lineas_n) + "\n\n---\n\n" + "\n\n".join(lineas_h)
    try:
        raw_conf = _llm(sistema, usuario, esfuerzo="high")
        data = _json_robusto(raw_conf) or {}
    except Exception as e:
        RESUMEN["errores"].append(f"detectar_confusion_con_heredadas: {e}")
        return {}
    if not isinstance(data, dict):
        return {}
    ids_n = {h["id"] for h in nuevas}
    ids_h = {h["id"] for h in heredadas}
    fuera = {}
    for k, v in data.items():
        k, v = str(k).strip().upper(), str(v).strip().upper()
        if k in ids_n and v in ids_h:
            fuera[k] = v
    return fuera


def destino_de(h: dict, mapa_hnnn: dict[str, str]) -> dict | None:
    """Donde va esta hipotesis: nota existente (UPDATE) o nota nueva (CREATE).

    DETERMINISTA. Sin busqueda semantica, sin LLM, sin umbrales.

    La regla es una sola: una hipotesis HEREDADA ya tiene su nota en Fortemi, y
    se sabe cual -- la que lleva su tag H-NNN. Todo lo demas es nuevo y se crea.

    POR QUE SE QUITO LO ANTERIOR. La version con busqueda semantica usaba el
    score de /search contra un umbral de 0.80. Ese score esta normalizado min-max
    (crates/matric-search/src/rsf.rs), asi que el primer resultado de cualquier
    busqueda sale SIEMPRE con 1.00, sea buen parecido o basura. El umbral no
    comparaba nada. Consecuencia real: las hipotesis nuevas de un periodo se
    metieron dentro de las notas del anterior -- varias en la MISMA nota -- y una
    falsada quedo enterrada dentro de una viva. Ninguna se creo.

    Sustituirlo por un juicio de LLM tampoco servia: cambiaba fagocitar por
    duplicar, y ademas metia una llamada por hipotesis para responder algo que
    el tag ya dice sin ambiguedad.

    Si algun dia hace falta detectar que dos hipotesis de PERIODOS DISTINTOS son
    la misma, ese es el trabajo de juicio_de_fusion (criterio holistico), no
    de este paso, y se ejecuta como fusion explicita y registrada -- no como un
    append silencioso dentro de otra nota.
    """
    # Fusion EXPLICITA Y REGISTRADA: el debate propuso esto como si fuera nuevo,
    # pero un juicio aparte (detectar_confusion_con_heredadas) encontro que es
    # el mismo hallazgo que una heredada. "El parser lo asume" -- trata esto
    # exactamente como si el debate hubiera reforzado la heredada, que es lo
    # que deberia haber pasado.
    objetivo = h.get("_absorber_en_heredada")
    if objetivo:
        nid = mapa_hnnn.get(str(objetivo).strip().upper())
        if nid:
            return {"note_id": nid,
                    "por": f"el debate la propuso como nueva pero es {objetivo}"}

    if not h.get("heredada"):
        return None
    nid = mapa_hnnn.get(str(h.get("id", "")).strip().upper())
    if not nid:
        return None
    return {"note_id": nid, "por": "heredada con tag " + str(h.get("id"))}


def escribir(h: dict, numero: int, periodo: str, sid: str | None, args,
             mapa_hnnn: dict[str, str] | None = None) -> None:
    hid_local = h["id"]
    estatus = h.get("status", "nuevo")
    concepto = ESTATUS.get(estatus)
    if not concepto:
        RESUMEN["omitidas"].append({"id": hid_local, "estado": estatus, "por": "estatus desconocido"})
        return

    alimentada = False
    tema = tema_de(h)
    if not args.sin_renombrar and not h.get("_collection_resuelta"):
        tema = nombrar_collection(tema, [h] + h.get("_absorbidas", []))
        h["collection"] = tema
    # tag_h ANTES de redactar: es lo que titula la nota, y tiene que coincidir
    # con lo que despues se escribe como tag en Fortemi. Calcularlo despues fue
    # el bug -- la nota se titulaba con el id local del debate y se tageaba con
    # otro numero.
    tag_h = f"H-{numero:03d}"
    # Al alimentar no se redacta la nota entera: solo el delta. Se decide aqui
    # para no pagar la lectura de todas las notas citadas dos veces.
    es_alimentacion = bool(destino_de(h, mapa_hnnn or {}))
    contenido = "" if es_alimentacion else redactar(h, periodo, args, tag_h)
    tags = ["thesis", tag_h, f"periodo/{sanear_tag(periodo)}", f"estado/{sanear_tag(estatus)}"]

    if args.dry_run:
        log(f"[dry] {hid_local} -> {tag_h} [{estatus}->{concepto}] tema={tema!r} "
            f"{len(h.get('evidence',[]))} notas")
        RESUMEN["creadas"].append({"local": hid_local, "tag": tag_h, "estado": estatus,
                                   "collection": tema, "dry": True})
        return

    ya = destino_de(h, mapa_hnnn or {})
    if ya and ya.get("note_id"):
        # Solo una heredada llega aqui, y solo si este periodo la toco.
        tag_h = str(h.get("id", "")).strip().upper() or tag_h
        # Solo el delta, no la nota entera otra vez.
        bloque = ("\n\n---\n\n" + redactar_actualizacion(h, periodo, args, tag_h))
        if anadir_parrafo(ya["note_id"], bloque):
            anadir_tags(ya["note_id"], [f"periodo/{sanear_tag(periodo)}",
                                        f"estado/{sanear_tag(estatus)}"])
            RESUMEN["actualizadas"].append({"local": hid_local, "note_id": ya["note_id"],
                                            "por": ya["por"], "estado": estatus})
            extra = f" ({ya['por']})" if "propuso" in ya.get("por", "") else ""
            log(f"{hid_local} -> alimentada: {ya['note_id']} [{estatus}]{extra}")
            nota_id = ya["note_id"]
            alimentada = True
        else:
            RESUMEN["errores"].append(f"no se pudo actualizar {ya['note_id']}")
            return
    else:
        nota_id = crear_nota(contenido, tags)
        if not nota_id:
            RESUMEN["errores"].append(f"no se pudo crear la nota de {hid_local}")
            return
        RESUMEN["creadas"].append({"local": hid_local, "tag": tag_h, "note_id": nota_id,
                                   "estado": estatus, "collection": tema})
        log(f"{hid_local} -> {tag_h} nota {nota_id} [{estatus}]")

    if sid and concepto:
        cid = concept_id(concepto, sid)
        if cid:
            adjuntar_concept(nota_id, cid)

    # Vinculo consultable hipotesis -> notas citadas.
    citadas = list(dict.fromkeys((h.get("evidence") or []) + (h.get("counter_evidence") or [])))
    if not args.sin_etiquetar_citadas:
        for nid in citadas:
            if anadir_tags(nid, [f"cited/{tag_h}"]):
                RESUMEN["notas_etiquetadas"] += 1


    col = None if alimentada else collection_id(tema)
    h["_note_id"] = nota_id      # para que aplicar_emit_collection() lo encuentre despues
    h["_col_id"] = col
    if col:
        try:
            api("POST", f"/api/v1/notes/{nota_id}/move", json={"collection_id": col})
        except Exception as e:
            RESUMEN["errores"].append(f"move hipotesis {nota_id}: {e}")
        if not args.sin_mover_notas:
            por_id = {v: k for k, v in _COLLECTIONS.items()}
            for nid in citadas:
                n = get_note(nid)
                actual = (n or {}).get("collection_id")
                destino, destino_nombre = col, tema

                if actual and actual != col:
                    # La nota ya vive en otro tema.
                    #
                    # POR QUE ESTO ESTA APAGADO POR DEFECTO: disparar el tema
                    # emergente cada vez que dos hipotesis comparten una nota
                    # citada se desboca. En una corrida con pocas hipotesis y unas
                    # decenas de notas citadas produjo once colecciones — tres
                    # nombres distintos para lo mismo, y ninguno contiene los
                    # temas de los que salio.
                    # Encadenaba: el hibrido de ayer era componente del hibrido de
                    # hoy, y cada encuentro pedia un nombre nuevo al LLM.
                    #
                    # La regla original era sobre una NOTA que participa de dos
                    # temas, no sobre cada coincidencia de cita. Hasta acotar bien
                    # el disparador, la nota se queda donde esta y el caso se
                    # reporta. Se activa con --temas-emergentes.
                    # La nota pertenece a dos temas. NO se decide nada aqui: se
                    # apunta el caso y al final de la corrida se decide todo junto,
                    # en una sola llamada. Decidirlo sobre la marcha fue lo que
                    # genero once colecciones para unas pocas hipotesis.
                    anterior = por_id.get(actual, actual)
                    PENDIENTES_DOS_TEMAS.setdefault(
                        frozenset({_norm(anterior), _norm(tema)}), []).append(
                            {"note_id": nid, "esta_en": anterior, "tambien_en": tema,
                             "por": tag_h})
                    continue
                if destino == actual:
                    continue
                try:
                    api("POST", f"/api/v1/notes/{nid}/move", json={"collection_id": destino})
                except Exception as e:
                    RESUMEN["errores"].append(f"move {nid} -> {destino_nombre}: {e}")
    return not alimentada


# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="Etapa 7: materializa las hipotesis en Fortemi")
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--period", required=True)
    ap.add_argument("--dry-run", action="store_true", help="No escribe nada. Solo dice que haria.")
    ap.add_argument("--solo-consolidar", action="store_true",
                    help="No procesa hipotesis: solo mira los temas que ya existen en "
                         "Fortemi y funde los que son el mismo. Usalo con --dry-run "
                         "primero.")
    ap.add_argument("--sin-consolidar", action="store_true",
                    help="No consolidar temas al terminar")
    ap.add_argument("--sin-temas-emergentes", action="store_true",
                    help="No decidir temas emergentes. Las notas de dos temas se quedan "
                         "donde estan y los casos salen en notas_de_dos_temas.")
    ap.add_argument("--sin-mover-notas", action="store_true",
                    help="No mover las notas citadas a la collection. Por defecto SI se "
                         "mueven: la coleccion agrupa el material del tema, no solo "
                         "las tesis. Una nota que ya vivia en otro tema no se arranca de "
                         "alli: va al tema emergente que contiene a los dos.")
    ap.add_argument("--sin-etiquetar-citadas", action="store_true",
                    help="No poner el tag cited/H-NNN en las notas citadas. Por defecto SI "
                         "se pone: escribe un tag en tus notas fuente. Los tags se acumulan, "
                         "una nota citada por H-001 y H-005 lleva los dos.")
    ap.add_argument("--max-tokens-llm", type=int, default=0,
                    help="Tope de tokens por llamada al LLM. 0 = sin tope (por defecto). "
                         "Ojo: el razonamiento sale del mismo presupuesto.")
    ap.add_argument("--sin-resolver-collections", action="store_true",
                    help="No buscar si la hipotesis pertenece a un tema que ya existe. "
                         "Se usa tal cual la etiqueta que escribio el agente.")
    ap.add_argument("--resolver-en-seco", action="store_true",
                    help="Resolver collections tambien en --dry-run (consulta Fortemi y gasta LLM)")
    ap.add_argument("--sin-renombrar", action="store_true",
                    help="No pedir nombre legible para collections que vengan como "
                         "codigo tecnico o trozo de pattern")
    ap.add_argument("--sin-fusion", action="store_true",
                    help="No detectar ni juzgar fusiones. Solo ejecuta los duplicate_of "
                         "que ya venian del debate.")
    ap.add_argument("--por-candidatos", action="store_true",
                    help="Pre-agrupar con la heuristica archivada antes de juzgar. Solo si "
                         "hay tantas hipotesis que no caben en una llamada.")
    ap.add_argument("--fusionar-estatus-mixto", action="store_true",
                    help="Permitir fusionar hipotesis de estados distintos (p.ej. una "
                         "falsada dentro de una viva). Sin decidir: por defecto NO, y los "
                         "grupos mixtos se reportan en el resumen.")
    ap.add_argument("--solape-mecanismo", type=float, default=0.35,
                    help="Solo con --por-candidatos")
    ap.add_argument("--pendientes", help="Ruta del archivo central de pendientes")
    ap.add_argument("--sin-dedup", action="store_true", help="No buscar equivalentes en Fortemi")
    ap.add_argument("--umbral-dedup", type=float, default=0.80)
    ap.add_argument("--max-notas", type=int, default=12, help="Notas cuyo texto se copia por hipotesis")
    ap.add_argument("--max-chars-nota", type=int, default=1200)
    args = ap.parse_args()

    global TOPE_TOKENS
    TOPE_TOKENS = args.max_tokens_llm

    run_dir = Path(args.run_dir)
    RESUMEN["periodo"] = args.period
    RESUMEN["dry_run"] = args.dry_run

    if args.solo_consolidar:
        log("consolidando temas existentes" + (" (en seco)" if args.dry_run else ""))
        consolidar_collections(args.dry_run)
        salida = run_dir / "consolidacion.json"
        salida.write_text(json.dumps(RESUMEN, ensure_ascii=False, indent=2), encoding="utf-8")
        if not RESUMEN["consolidaciones"]:
            log("nada que consolidar.")
        log(f"resumen -> {salida}")
        return 1 if RESUMEN["errores"] else 0

    hs = cargar_hipotesis(run_dir)

    # ── Quien es quien, ANTES de decidir nada ────────────────────────────────
    #
    # ESTE ORDEN IMPORTA Y ANTES ESTABA MAL. El enrutado corria sobre TODAS las
    # hipotesis del fichero, heredadas incluidas. Eso significaba pedirle
    # al modelo que clasificara tambien las que venian de periodos anteriores:
    # varias ni siquiera se iban a escribir, y las heredadas YA tienen su nota en
    # Fortemi con su coleccion puesta. No habia nada que enrutar en ellas, y ademas
    # arrastraban la decision, porque sus asuntos entraban al prompt como material
    # nuevo a clasificar.
    #
    # Una heredada no se re-enruta: se queda en la coleccion donde ya vive. Si
    # este periodo la alimento, se le anade un bloque a su nota y punto.
    for h in hs:
        if (h.get("heredada") or h.get("proposed_by") == "periodo_anterior") \
                and "tocada" not in h:
            # Los theory.json ya escritos no traen la marca: se deduce del
            # historial. Si hay algun evento de ESTE periodo, se examino aqui.
            h["tocada"] = any(str(e.get("period") or "") == str(args.period)
                              for e in (h.get("history") or []))
            h["heredada"] = True

    en_nuevo = [h for h in hs if h.get("status") in NO_ESCRIBIR]
    no_tocadas = [h for h in hs if h.get("heredada") and not h.get("tocada")
                  and h.get("status") not in NO_ESCRIBIR]
    alimentadas = [h for h in hs if h.get("heredada") and h.get("tocada")
                   and h.get("status") not in NO_ESCRIBIR]
    del_periodo = [h for h in hs if not h.get("heredada")
                   and h.get("status") not in NO_ESCRIBIR]

    # El debate se pudo confundir: propuso como nueva una hipotesis que en
    # realidad es una heredada dicha otra vez. Se saca ANTES de enrutar campo o
    # de fusionar entre nuevas, porque no es ni lo uno ni lo otro -- es
    # alimentacion que el debate deberia haber hecho y no hizo.
    todas_heredadas = [h for h in hs if h.get("heredada")
                       and h.get("status") not in NO_ESCRIBIR]
    if not args.sin_fusion and del_periodo and todas_heredadas:
        confundidas = detectar_confusion_con_heredadas(del_periodo, todas_heredadas)
        if confundidas:
            for h in del_periodo:
                objetivo = confundidas.get(h["id"])
                if objetivo:
                    h["_absorber_en_heredada"] = objetivo
                    RESUMEN["confusiones_con_heredadas"].append(
                        {"propuesta_como_nueva": h["id"], "es_en_realidad": objetivo})
                    log(f"  el debate propuso {h['id']} como nueva, pero es {objetivo}: "
                        f"se alimenta en vez de crearse")
            del_periodo = [h for h in del_periodo if not h.get("_absorber_en_heredada")]

    log(f"{len(hs)} en el fichero · {len(del_periodo)} de {args.period} · "
        f"{len(alimentadas)} heredada(s) alimentada(s) · "
        f"{len(no_tocadas)} heredada(s) sin tocar · {len(en_nuevo)} en NUEVO")

    for h in no_tocadas:
        RESUMEN["omitidas"].append({"id": h["id"], "estado": h.get("status"),
                                    "por": "heredada que este periodo no examino"})
    for h in en_nuevo:
        RESUMEN["omitidas"].append({"id": h["id"], "estado": h.get("status"),
                                    "por": "nadie la examino"})

    # ── El campo: SOLO para las de este periodo ──────────────────────────────
    if del_periodo and not args.sin_resolver_collections \
            and (not args.dry_run or args.resolver_en_seco):
        resolver_collections(del_periodo)

    # ── Merge A: tambien solo entre las de este periodo ──────────────────────
    # Fusionar una hipotesis nueva DENTRO de una heredada la entierra: no llega a
    # tener nota propia y su contenido queda como un bloque dentro de otra. Si de
    # verdad dice lo mismo que una anterior, el mecanismo correcto es que el
    # debate la refuerce (reinforce_hypothesis), no que el parser la absorba en
    # silencio.
    if not args.sin_fusion and len(del_periodo) > 1:
        juzgables = [h for h in del_periodo if not h.get("duplicate_of")]
        candidatas = (candidatos_de_fusion(juzgables, args.solape_mecanismo)
                      if args.por_candidatos else [juzgables])
        for grupo in candidatas:
            for conj in juicio_de_fusion(grupo, args.fusionar_estatus_mixto):
                canon, resto = conj[0], conj[1:]
                RESUMEN["fusiones_llm"].append({"canonica": canon, "absorbe": resto})
                log(f"  fusion: {'+'.join(conj)} -> {canon}")
                for h in del_periodo:
                    if h["id"] in resto and not h.get("duplicate_of"):
                        h["duplicate_of"] = canon
                        h.setdefault("history", []).append(
                            {"event": "marcada_duplicada", "by": f"parser/{LLM_MODEL}",
                             "ts": datetime.now(timezone.utc).isoformat(),
                             "reason": f"mismo falsador que {canon}"})

    vivas, absorbidas = absorber(hs)
    escribibles = [h for h in vivas
                   if h.get("status") not in NO_ESCRIBIR
                   and not (h.get("heredada") and not h.get("tocada"))]

    log(f"{len(escribibles)} se escriben"
        + (f" ({len(absorbidas)} absorbidas por duplicate_of)" if absorbidas else ""))

    sid = None
    if not args.dry_run:
        sid = scheme_id()
        log(f"scheme de estatus: {sid}")

    mapa_hnnn = {} if args.dry_run else notas_por_hnnn()
    numero = 1 if args.dry_run else siguiente_numero()
    log(f"la numeracion arranca en H-{numero:03d}"
        + (f" ({len(mapa_hnnn)} ya escritas en Fortemi)" if mapa_hnnn else ""))

    # ── El mapa de referencias, ANTES de escribir nada ───────────────────────
    #
    # Los agentes se refieren a las hipotesis por su ID LOCAL del debate ("como
    # dice H-059", "eso es H-044, no anclaje"). El parser las materializa con
    # OTRO numero: H-059 local puede acabar siendo H-038 en Fortemi. El texto de
    # la nota conserva el numero local, asi que quien lea la nota en Fortemi y
    # busque H-059 no encuentra nada -- o peor, encuentra otra hipotesis
    # distinta que si tiene ese numero.
    #
    # Para reescribir esas referencias hay que conocer el mapa ENTERO antes de
    # redactar la primera nota, no ir descubriendolo dentro del bucle. Por eso
    # los numeros se reparten aqui y el bucle solo los consume.
    # TODA hipotesis de `escribibles` recibe su numero aqui, incluidas las que
    # luego no consumiran uno nuevo. Si alguna se queda sin `_numero`, cae al
    # contador del bucle -- que para entonces ya avanzo por las anteriores -- y
    # la nota se titula con un numero distinto del que se le tagea.
    mapa_local = {}
    n_provisional = numero
    for h in escribibles:
        clave = str(h["id"]).upper()
        if destino_de(h, mapa_hnnn or {}):
            # Alimentada: conserva el numero que ya tiene en Fortemi.
            h["_numero"] = n_provisional      # no se usa, pero deja el campo puesto
            mapa_local[clave] = clave
            continue
        h["_numero"] = n_provisional
        mapa_local[clave] = f"H-{n_provisional:03d}"
        n_provisional += 1
    # Las absorbidas no tienen nota propia: su referencia apunta a la canonica.
    for a in RESUMEN["absorbidas"]:
        destino = mapa_local.get(str(a.get("en", "")).upper())
        if destino:
            mapa_local[str(a.get("id", "")).upper()] = destino
    # Las heredadas llegan con el ID que tenian en el theory.json anterior, y
    # los agentes las citan con ese numero. Su nota ya existe en Fortemi con su
    # tag, asi que el mapeo sale de ahi: si el buzon trae H-044 y en Fortemi hay
    # una nota tageada h-044, la referencia ya es correcta. Pero si el ID local
    # NO existe como tag, hay que resolverlo contra los resumenes anteriores.
    for h in hs:
        clave = str(h.get("id", "")).upper()
        if clave and clave not in mapa_local and clave in (mapa_hnnn or {}):
            mapa_local[clave] = clave      # su numero local YA es el de Fortemi
    for run_previo in sorted(CORPUS.glob("run_*")):
        res = run_previo / "parser_resumen.json"
        if not res.exists():
            continue
        try:
            d = json.loads(res.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        if str(d.get("periodo") or "") == str(args.period):
            continue                      # el de esta misma corrida, aun sin escribir
        for c in (d.get("creadas") or []):
            local = str(c.get("local") or "").upper()
            tag = str(c.get("tag") or "").upper()
            if local and tag:
                mapa_local.setdefault(local, tag)
        for a in (d.get("absorbidas") or []):
            local = str(a.get("id") or "").upper()
            canon = str(a.get("en") or "").upper()
            if local and canon:
                mapa_local.setdefault(local, mapa_local.get(canon, canon))
    REFERENCIAS.update(mapa_local)

    # El numero ya lo asigno el reparto de arriba. El contador del bucle sobra:
    # mantenerlo hacia que las dos numeraciones divergieran en cuanto una
    # hipotesis no consumia numero, y la nota acababa titulada con un numero y
    # tageada con otro.
    for h in escribibles:
        try:
            escribir(h, h["_numero"], args.period, sid, args, mapa_hnnn)
        except Exception as e:
            RESUMEN["errores"].append(f"{h.get('id')}: {e}")
            log(f"ERROR en {h.get('id')}: {e}")

    # Las absorbidas se registran, no se escriben como hipotesis aparte.
    for a in absorbidas:
        RESUMEN["omitidas"].append({"id": a["id"], "estado": "deprecated",
                                    "por": f"absorbida en {a['_absorbida_en']}"})

    # La señal de emit_collection: notas que van con un tema sin ser evidencia.
    aplicar_emit_collection(run_dir, escribibles, args)

    # ── Temas emergentes: una decision, al final, con todos los casos delante ──
    if PENDIENTES_DOS_TEMAS:
        log(f"{sum(len(v) for v in PENDIENTES_DOS_TEMAS.values())} nota(s) pertenecen a dos "
            f"temas, en {len(PENDIENTES_DOS_TEMAS)} combinacion(es).")
        for temas, casos in PENDIENTES_DOS_TEMAS.items():
            RESUMEN["notas_de_dos_temas"].extend(casos)
        decididos = ({} if (args.dry_run and not args.resolver_en_seco) or args.sin_temas_emergentes
                     else decidir_temas_emergentes(PENDIENTES_DOS_TEMAS))
        for temas, nombre in decididos.items():
            casos = PENDIENTES_DOS_TEMAS.get(temas) or []
            etiquetas = sorted({c["esta_en"] for c in casos} | {c["tambien_en"] for c in casos})
            # NO sembrar _HIBRIDOS aqui: collection_hibrida mira ese cache al
            # entrar y, si encuentra el nombre, hace _COLLECTIONS[_norm(nombre)]
            # dando por hecho que la collection existe. Sembrarlo antes de
            # crearla provocaba KeyError con el nombre del tema.
            h_ = collection_hibrida(temas, etiquetas, nombre_sugerido=nombre)
            if not h_:
                continue
            nombre_final, cid = h_
            for c in casos:
                if args.dry_run:
                    continue
                try:
                    api("POST", f"/api/v1/notes/{c['note_id']}/move",
                        json={"collection_id": cid})
                    RESUMEN["hibridos"].append({**c, "va_a": nombre_final})
                except Exception as e:
                    RESUMEN["errores"].append(f"move {c['note_id']} -> {nombre_final}: {e}")
        if not decididos:
            log("  ninguna combinacion converge en un tema mas amplio. Se quedan donde estan.")

    # ── Archivo central de pendientes ────────────────────────────────────────
    # Las NUEVO no entran a Fortemi (nadie las examino) pero no se pierden: van a
    # un solo fichero que el prep carga en todas las corridas siguientes. En
    # cuanto dejan de estar en NUEVO, salen de aqui.
    pend_path = Path(args.pendientes) if args.pendientes else run_dir.parent / "pendientes.json"
    try:
        actual = {}
        if pend_path.exists():
            for h in (json.loads(pend_path.read_text(encoding="utf-8")) or {}).get("pendientes", []):
                actual[h.get("_clave") or h.get("id")] = h
        # entran las nuevas
        for h in vivas:
            if h.get("status") in NO_ESCRIBIR:
                clave = f"{args.period}:{h['id']}"
                h2 = {k: v for k, v in h.items() if not k.startswith("_")}
                h2["_clave"] = clave
                h2["_periodo_origen"] = args.period
                actual[clave] = h2
                RESUMEN["pendientes"].append(clave)
        # salen las que ya se examinaron
        # Se emparejan por _clave, NO por id: el prep renumera las pendientes al
        # cargarlas (les da el numero que les tocaria en Fortemi ahora), asi que
        # el id que viaja en el debate ya no es el que esta guardado aqui.
        # Comparar por id dejaria las pendientes atrapadas en el archivo para
        # siempre, aunque el periodo las hubiera debatido.
        examinadas_clave = {h.get("_clave") for h in escribibles if h.get("_clave")}
        examinadas_clave |= {a.get("_clave") for a in absorbidas if a.get("_clave")}
        examinadas_id = {h["id"] for h in escribibles} | {a["id"] for a in absorbidas}
        for clave in list(actual):
            if clave.startswith(args.period + ":"):
                continue
            if clave in examinadas_clave or actual[clave].get("id") in examinadas_id:
                actual.pop(clave)
        if not args.dry_run:
            pend_path.write_text(json.dumps(
                {"actualizado": datetime.now(timezone.utc).isoformat(),
                 "pendientes": list(actual.values())}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        log(f"pendientes (NUEVO sin examinar): {len(actual)} en {pend_path.name}")
    except Exception as e:
        RESUMEN["errores"].append(f"pendientes: {e}")

    # Consolidar al final: los temas de esta corrida ya estan escritos, asi que
    # el juez ve el estado real, no una prediccion.
    if not args.sin_consolidar and not args.dry_run:
        log("consolidando temas:")
        consolidar_collections(False)
        if not RESUMEN["consolidaciones"]:
            log("  nada que fundir todavia.")

    salida = run_dir / "parser_resumen.json"
    salida.write_text(json.dumps(RESUMEN, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    if RESUMEN["fusiones_estatus_mixto"]:
        log(f"AVISO: {len(RESUMEN['fusiones_estatus_mixto'])} grupo(s) que el LLM daria por "
            f"iguales tienen estados distintos. NO se fusionaron. Estan en el resumen.")
    if RESUMEN["emit_collection"]:
        log(f"emit_collection aplicadas: {len(RESUMEN['emit_collection'])} señal(es)")
    if RESUMEN["fusiones_llm"]:
        log(f"fusiones decididas por el LLM: {len(RESUMEN['fusiones_llm'])}")
    if RESUMEN["collections_renombradas"]:
        log(f"collections renombradas: " + ", ".join(
            f"{r['de']} -> {r['a']}" for r in RESUMEN["collections_renombradas"][:6]))
    # El contador mide DOS resultados distintos, no uno. Un periodo que solo
    # alimenta hipotesis anteriores y no crea ninguna es un buen periodo: la
    # herencia esta haciendo su trabajo. Contar solo "creadas" empuja a producir
    # hipotesis nuevas para que el numero suba, que es justo lo que no se quiere.
    nuevas = [c for c in RESUMEN["creadas"]]
    alim = RESUMEN["actualizadas"]
    por_estado = {}
    for c in nuevas + alim:
        por_estado[c.get("estado", "?")] = por_estado.get(c.get("estado", "?"), 0) + 1
    sin_examinar = [o for o in RESUMEN["omitidas"] if "no examino" in str(o.get("por", ""))]
    log(f"nuevas de {RESUMEN['periodo']}   : {len(nuevas)}")
    if alim:
        origenes = {}
        for a in alim:
            origenes[a.get("estado", "?")] = origenes.get(a.get("estado", "?"), 0) + 1
        log(f"alimentadas          : {len(alim)}  ("
            + ", ".join(f"{k}={v}" for k, v in sorted(origenes.items())) + ")")
    else:
        log(f"alimentadas          : 0  (ninguna hipotesis anterior se examino aqui)")
    log(f"heredadas sin tocar  : {len(sin_examinar)}")
    log(f"por estado           : "
        + (", ".join(f"{k}={v}" for k, v in sorted(por_estado.items())) or "-"))
    log(f"absorbidas={len(RESUMEN['absorbidas'])} omitidas={len(RESUMEN['omitidas'])} "
        f"notas_etiquetadas={RESUMEN['notas_etiquetadas']}")
    if RESUMEN["notas_de_dos_temas"]:
        log(f"{len(RESUMEN['notas_de_dos_temas'])} nota(s) citadas pertenecen a mas de un "
            f"tema. Se quedaron donde estaban. Estan en el resumen.")
    if RESUMEN["hibridos"]:
        log(f"{len(RESUMEN['hibridos'])} nota(s) de dos temas fueron al tema emergente:")
        for x in RESUMEN["hibridos"][:6]:
            print(f"   · {x.get('esta_en') or x.get('estaba_en','?')} + "
                  f"{x.get('tambien_en') or x.get('citada_por','?')} -> {x.get('va_a','?')}")
    if RESUMEN["migraciones"]:
        log(f"{len(RESUMEN['migraciones'])} nota(s) migraron desde otra collection. "
            f"De donde venian esta en el resumen.")
    if RESUMEN["errores"]:
        log(f"{len(RESUMEN['errores'])} errores. Los primeros:")
        for e in RESUMEN["errores"][:5]:
            print(f"   · {e}")
    log(f"resumen -> {salida}")
    return 1 if RESUMEN["errores"] else 0


if __name__ == "__main__":
    sys.exit(main())