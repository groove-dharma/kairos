#!/usr/bin/env python3
"""E2 Debate — etapa 6 del pipeline forkeado.

Reemplaza el selective coding de una sola llamada de GTFlow por un debate de dos
agentes en conversation mode de AG2.

Dos agentes, adapter `conversation`, sin kicker, sin handoff, sin TransitionGraph.
Cada uno defiende una categoria central distinta a partir de las triples axiales.
Producen varias hipotesis sobrevivientes, no una teoria unica.

Los agentes NO escriben a Fortemi. Leen (MCP read-only), buscan afuera (SearXNG) y
depositan sus conclusiones via tools locales que escriben a disco. El parser
determinista de la etapa 7 es quien materializa en Fortemi.

Uso:
    python e2_debate.py --input /app/corpus/run_<periodo>/debate_input.json \
                        --out-dir /app/corpus/run_<periodo> --max-rounds 15
"""

import argparse
import asyncio
import functools
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone

import httpx

from ag2 import Agent, KnowledgeConfig, tool
from ag2.config import OpenAIConfig
from ag2.knowledge import MemoryKnowledgeStore, SqliteKnowledgeStore
from ag2.middleware import BaseMiddleware, LoggingMiddleware, Middleware, TokenLimiter
from ag2.network import EV_CHANNEL_CLOSED, EV_PACKET, EV_TEXT, Hub
from ag2.policies import SlidingWindowPolicy, WorkingMemoryPolicy
from ag2.tools import MCPServerConfig, MCPToolkit

logging.basicConfig(
    level=logging.INFO, stream=sys.stdout,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s", datefmt="%H:%M:%S",
)
for noisy in ("httpx", "httpcore", "mcp"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

print = functools.partial(print, flush=True)

DATA_DIR = os.environ.get("DATA_DIR", "/app/data")
REPORTS_DIR = os.environ.get("REPORTS_DIR", "/app/reports")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

PRECIOS = {
    "gemma4-31b":       {"in": 0.40, "out": 1.00},
    "deepseek-v4-flash": {"in": 0.30, "out": 0.70},
    "gpt-oss-120b":     {"in": 0.15, "out": 0.60},
}

# 12.000 caracteres: la nota entera en casi todos los casos. Cabe porque el
# TokenLimiter subio a 64K — gemma4-31b y deepseek-v4-flash aguantan mucho mas, pero
# el bug del parser DSML de DeepSeek aparece sobre los 95K de contexto y no
# conviene acercarse.
MAX_TOOL_RESULT_CHARS = 12_000

PRESUPUESTO = {"usd": 0.0, "llamadas": 0, "tope_usd": 0.50, "tope_llamadas": 400}

BUZON: dict = {"hypotheses": [], "collections": []}

MATERIAL: str = ""

BUZON_JSONL = {"path": ""}

# Detector de degeneracion. Un agente puede entrar en bucle de "voy a consultar
# las tools" sin ejecutar ninguna y sin producir contenido: se come los minutos
# del turno y deja sus hipotesis sin defender. Se cuentan los turnos seguidos sin
# ninguna tool ejecutada; a partir de un umbral se corta.
IMPRODUCTIVOS = {"agente_a": 0, "agente_b": 0}
# Turnos cerrados sin texto ni tools: el adapter no envia nada y el canal se para.
TURNOS_VACIOS = {"agente_a": 0, "agente_b": 0}
# Marca de tiempo de la ultima llamada al modelo. Un turno con 20 tool calls tarda
# minutos sin emitir mensaje: sin esto, el detector de canal parado mata un agente
# que esta trabajando.
ULTIMA_ACTIVIDAD = {"t": 0.0}

# Consultas cuyo resultado no cambia dentro de un turno: se cachean para poder
# devolverlas si el agente las vuelve a pedir tras un recorte de contexto.
TOOLS_CACHEABLES = {
    "listar_codigos", "notas_por_codigo", "list_hypotheses",
    "buscar_hipotesis_fortemi", "get_note", "get_full_document",
}

# Tools que PRODUCEN: nunca se bloquean ni cuentan para el tope por turno. El tope
# esta para cortar bucles de consulta, no para impedir que se registre trabajo hecho.
TOOLS_PRODUCTIVAS = {
    "propose_hypothesis", "challenge_hypothesis", "defend_hypothesis",
    "refine_hypothesis", "concede_hypothesis", "reinforce_hypothesis",
    "emit_collection", "report_delta",
    # `say` emite el mensaje al otro agente: es lo que hace que el debate SEA un
    # debate. Bloquearla no ahorra ninguna consulta -- no consulta nada -- y en
    # cambio corta el turno: un agente escribio un resumen de estado y se lo
    # tragaron con "Agotaste tus 20 consultas". El tope esta para cortar bucles
    # de lectura, no para amordazar.
    "say",
}

# Registro de todas las llamadas a tools, para auditar despues que hizo cada agente.
TOOLS_LOG: list = []
TOOLS_LOG_PATH = {"path": ""}


def _log_tool(agente: str, tool: str, args: str, estado: str, segs: float) -> None:
    fila = {"ts": datetime.now(timezone.utc).isoformat(), "agente": agente,
            "tool": tool, "estado": estado, "segs": segs,
            "args": args[:400] if args else ""}
    TOOLS_LOG.append(fila)
    if TOOLS_LOG_PATH["path"]:
        try:
            with open(TOOLS_LOG_PATH["path"], "a", encoding="utf-8") as f:
                f.write(json.dumps(fila, ensure_ascii=False) + "\n")
        except Exception:
            pass
TOOLS_ESTE_TURNO = {"agente_a": 0, "agente_b": 0}

REST_TOKEN: dict = {"value": ""}
PERIODO: dict = {"label": "", "start": None, "end": None}
NOTE_IDS_SEMILLA: set = set()
_CACHE_FECHA: dict = {}
# SIN anclas ^$ y con mayusculas: se usa con .findall() para extraer ids de un
# texto libre, no solo para validar una cadena entera. Con ^...$ findall no
# encontraria nada. Para validar una cadena completa se usa .fullmatch().
UUID_RE = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-'
                     r'[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')
CURRENT_AGENT: dict = {"name": ""}


def _persistir(tipo: str, payload: dict) -> None:
    if not BUZON_JSONL["path"]:
        return
    try:
        with open(BUZON_JSONL["path"], "a", encoding="utf-8") as f:
            f.write(json.dumps({"tipo": tipo, "ts": datetime.now(timezone.utc).isoformat(),
                                **payload}, ensure_ascii=False) + "\n")
    except Exception as e:
        _audit({"timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "persist_error", "error": str(e)})

INDICE_CODIGOS: dict = {}

# Claves del indice que son categorias del libro de codigos (no codigos crudos).
# Son las que significan algo: el open coding genera cientos de codigos casi unicos.
CATEGORIAS_INDEXADAS: set = set()


def _norm(s: str) -> str:
    """Normaliza un nombre de codigo: minusculas, separadores a guion bajo."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def _tokens(s: str) -> set:
    return {t for t in _norm(s).split("_") if len(t) > 2}


def cargar_indice_codigos(datos: dict, input_path: str) -> tuple[int, int, float]:
    ruta = datos.get("code_index")
    if not ruta or not os.path.exists(ruta):
        alt = os.path.join(os.path.dirname(input_path), "code_index.json")
        ruta = alt if os.path.exists(alt) else None
    if not ruta:
        return 0, 0, 0.0

    with open(ruta, encoding="utf-8") as f:
        blob = json.load(f)
    INDICE_CODIGOS.update(blob.get("index") or {})
    CATEGORIAS_INDEXADAS.update(blob.get("categorias_indexadas") or [])

    st = blob.get("verificacion") or {}
    tot = (st.get("verificadas", 0) or 0) + (st.get("descartadas", 0) or 0)
    pct = (st.get("verificadas", 0) / tot * 100) if tot else 0.0
    notas = len({e["note_id"] for v in INDICE_CODIGOS.values() for e in v})
    return len(INDICE_CODIGOS), notas, pct


def _fecha_nota(note_id: str) -> str | None:
    if note_id in _CACHE_FECHA:
        return _CACHE_FECHA[note_id]
    rest = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000").rstrip("/")
    valor = None
    try:
        r = httpx.get(f"{rest}/api/v1/notes/{note_id}",
                      headers={"Authorization": f"Bearer {REST_TOKEN['value']}"},
                      timeout=20.0)
        if r.status_code == 200:
            d = r.json()
            d = d.get("note", d)
            valor = d.get("created_at_utc") or d.get("created_at")
    except Exception:
        valor = None
    _CACHE_FECHA[note_id] = valor
    return valor


def _en_periodo(note_id: str) -> tuple[bool, str]:
    if note_id in NOTE_IDS_SEMILLA:
        return True, "semilla"
    if not UUID_RE.fullmatch(str(note_id).strip()):   # fullmatch: el patron ya no lleva ^$
        return False, "no es un note_id completo (parece truncado)"
    if not PERIODO["start"]:
        return True, "sin limites de periodo"
    f = _fecha_nota(note_id)
    if f is None:
        return True, "no verificable"
    if PERIODO["start"] <= f <= PERIODO["end"]:
        return True, "verificada"
    return False, f"fuera del periodo ({f[:10]})"


def _audit(entry: dict) -> None:
    with open(os.path.join(REPORTS_DIR, "e2_debate_audit.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


class E2Middleware(BaseMiddleware):
    def __init__(self, event, context, agent_name="", modelo="", max_tool_calls=20):
        super().__init__(event, context)
        self.agent_name = agent_name
        self.modelo = modelo
        self.max_tool_calls = max_tool_calls
        self.tool_call_count = 0
        self.cache: dict = {}
        self.ultimo_estado = None   # huella del estado la ultima vez que se listo
        self.blocked = False

    async def on_llm_call(self, call_next, events, context):
        ULTIMA_ACTIVIDAD["t"] = time.time()
        start = time.time()
        n_events = len(events) if hasattr(events, "__len__") else 0
        CURRENT_AGENT["name"] = self.agent_name
        # FIX: NO resetear blocked ni tool_call_count aquí.
        # Esos se inicializan en __init__ (una vez por turno).
        # Resetearlos aquí hacía que el cap nunca disparara.

        response = await call_next(events, context)
        elapsed = time.time() - start

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.agent_name, "type": "llm_call",
            "n_events": n_events, "elapsed_secs": round(elapsed, 2),
        }
        usage = getattr(response, "usage", None)
        if usage:
            pt = getattr(usage, "prompt_tokens", 0) or 0
            ct = getattr(usage, "completion_tokens", 0) or 0
            entry["prompt_tokens"] = pt
            entry["completion_tokens"] = ct
            p = PRECIOS.get(self.modelo, {"in": 0.30, "out": 0.70})
            coste = (pt / 1e6) * p["in"] + (ct / 1e6) * p["out"]
            PRESUPUESTO["usd"] += coste
            entry["coste_usd"] = round(coste, 6)
            entry["acumulado_usd"] = round(PRESUPUESTO["usd"], 4)
        PRESUPUESTO["llamadas"] += 1
        entry["llamadas_acumuladas"] = PRESUPUESTO["llamadas"]

        # ¿Cierra el turno? Un response sin tool_calls es la ultima llamada del turno.
        tool_calls = getattr(response, "tool_calls", None)
        contenido = getattr(response, "content", None)
        if not tool_calls:
            # Reset defensivo: si AG2 reutilizara la instancia de middleware entre
            # turnos, el cap se quedaria pegado y el agente no volveria a leer nunca.
            self.blocked = False
            self.tool_call_count = 0
            self.cache.clear()
            # Tambien la huella del estado: si no, la PRIMERA list_hypotheses del
            # turno siguiente recibiria "nada ha cambiado desde la ultima vez en
            # este turno" cuando es otro turno y el agente no recuerda el anterior.
            self.ultimo_estado = None
            # Y el contador de rechazos seguidos, que es por turno, no global.
            RECHAZOS_SEGUIDOS.clear()
            if not contenido:
                # Turno vacio: ni texto ni tools. El adapter no envia nada al canal,
                # asi que el otro agente nunca se dispara y la conversacion se para.
                TURNOS_VACIOS[self.agent_name] = TURNOS_VACIOS.get(self.agent_name, 0) + 1
                entry["turno_vacio"] = True
                print(f"\n[E2] {self.agent_name} cerró su turno SIN CONTENIDO "
                      f"({entry.get('completion_tokens', 0)} tokens generados y "
                      "descartados). Nada llega al canal.")
        _audit(entry)
        return response

    async def on_tool_execution(self, call_next, event, context):
        from ag2.events import ToolResultEvent
        tool_name = str(getattr(event, "name", event) or "")
        args = str(getattr(event, "arguments", "") or "")

        # Las tools que PRODUCEN nunca se bloquean ni cuentan para el tope. El tope
        # existe para cortar bucles de reconocimiento, no para impedir registrar.
        # Dos propose_hypothesis bien formadas pueden perderse si el agente ha
        # gastado todas sus llamadas consultando.
        if tool_name in TOOLS_PRODUCTIVAS:
            start = time.time()
            result = await call_next(event, context)
            _log_tool(self.agent_name, tool_name, args, "productiva",
                      round(time.time() - start, 2))
            # CACHE OBSOLETO. list_hypotheses esta cacheado por turno con clave
            # tool|args. Si el agente defiende H-001 y vuelve a pedir la lista con
            # los mismos argumentos, recibia la copia de ANTES de defender: seguia
            # viendola como desafiada y la defendia otra vez.
            # Cualquier tool que produce cambia el estado, asi que la vista del
            # estado deja de valer.
            if tool_name != "say":
                for clave in [k for k in self.cache
                              if k.startswith(("list_hypotheses|", "buscar_hipotesis_fortemi|"))]:
                    self.cache.pop(clave, None)
                self.ultimo_estado = None
            return result

        # Repeticion exacta dentro del mismo turno: mismo nombre y mismos argumentos.
        # No consume tope y corta el bucle sin castigar.
        # Repeticion exacta dentro del mismo turno. NO se reprocha: se devuelve el
        # resultado cacheado. El agente no repite por mañoso — cuando una tanda de
        # get_note inunda el contexto, el TokenLimiter recorta lo viejo y el resultado
        # anterior deja de existir para el. Pedirlo otra vez es lo racional; decirle
        # "míralo más arriba" seria mandarlo a mirar algo que ya no esta.
        firma = f"{tool_name}|{args}"

        # list_hypotheses devuelve ~12 KB. Llamarla muchas veces acumula cientos de
        # KB de contexto para releer lo mismo, empujando fuera el material del
        # periodo y comiendose el presupuesto. Si nada ha cambiado desde la
        # ultima vez que la pidio, no hace falta repetir el volcado entero: basta
        # decirselo. Si cambio algo, se devuelve completo.
        if tool_name == "list_hypotheses":
            huella = self._estado_hipotesis()
            if self.ultimo_estado is not None and huella == self.ultimo_estado:
                _log_tool(self.agent_name, tool_name, args, "sin-cambios", 0.0)
                return ToolResultEvent.from_call(
                    event,
                    "Nada ha cambiado desde la ultima vez que pediste la lista en este "
                    "turno: mismos ids, mismos estados, misma evidencia. Si perdiste el "
                    "hilo, esta en tu mensaje anterior. Sigue con lo que ibas a hacer.")
            self.ultimo_estado = huella

        if firma in self.cache:
            _log_tool(self.agent_name, tool_name, args, "repetida", 0.0)
            return ToolResultEvent.from_call(
                event,
                self.cache[firma] +
                "\n\n[Ya habías pedido esto en este turno; se te devuelve igual. Si no lo "
                "recordabas es porque el historial se recortó, no porque lo hicieras mal. "
                "Guárdalo en tu mensaje si lo vas a necesitar después.]",
            )

        if self.blocked:
            _log_tool(self.agent_name, tool_name, args, "bloqueada", 0.0)
            return ToolResultEvent.from_call(
                event,
                "Sin consultas este turno: agotaste las de reconocimiento. Pero las tools "
                "que PRODUCEN siguen abiertas: propose_hypothesis, challenge_hypothesis, "
                "defend_hypothesis, refine_hypothesis, concede_hypothesis, "
                "reinforce_hypothesis y emit_collection funcionan siempre. Registra lo que "
                "ya tengas o responde con texto.",
            )

        self.tool_call_count += 1
        if self.tool_call_count > self.max_tool_calls:
            self.blocked = True
            _audit({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "agent": self.agent_name, "type": "tool_blocked",
                "tool": tool_name, "count": self.tool_call_count,
            })
            _log_tool(self.agent_name, tool_name, args, "bloqueada", 0.0)
            return ToolResultEvent.from_call(
                event,
                f"Agotaste tus {self.max_tool_calls} consultas de este turno. Las tools de "
                "registro siguen disponibles: propose_hypothesis, challenge_hypothesis, "
                "defend_hypothesis, refine_hypothesis, concede_hypothesis, "
                "reinforce_hypothesis, emit_collection. Registra lo que ya tengas "
                "respaldado, o responde con TEXTO. En tu próximo turno recuperas las "
                "consultas.",
            )
        TOOLS_ESTE_TURNO[self.agent_name] = TOOLS_ESTE_TURNO.get(self.agent_name, 0) + 1
        start = time.time()
        result = await call_next(event, context)
        elapsed = round(time.time() - start, 2)

        texto = ""
        try:
            partes = getattr(getattr(result, "result", None), "parts", None) or []
            texto = "".join(getattr(x, "content", "") or "" for x in partes)
        except Exception:
            texto = ""

        truncado = False
        if texto and len(texto) > MAX_TOOL_RESULT_CHARS:
            from ag2.events import ToolResultEvent
            recorte = texto[:MAX_TOOL_RESULT_CHARS]
            result = ToolResultEvent.from_call(
                event,
                recorte + f"\n\n[...RECORTADO. La respuesta traía {len(texto):,} caracteres "
                          f"y se cortó en {MAX_TOOL_RESULT_CHARS:,}. Afina la consulta: "
                          f"usa filtros, menos limit, o una búsqueda más específica.]",
            )
            truncado = True

        _audit({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.agent_name, "type": "tool_call", "tool": tool_name,
            "elapsed_secs": elapsed, "count": self.tool_call_count,
            "chars": len(texto), "truncado": truncado,
        })
        _log_tool(self.agent_name, tool_name, args,
                  "truncada" if truncado else "ok", elapsed)

        # Cachear solo lo barato de devolver y estable dentro del turno
        if tool_name in TOOLS_CACHEABLES and texto:
            self.cache[f"{tool_name}|{args}"] = texto[:MAX_TOOL_RESULT_CHARS]
        return result

    def _estado_hipotesis(self) -> str:
        """Huella del estado actual, para saber si algo cambio desde la ultima vez."""
        return "|".join(f"{h['id']}:{h.get('status')}:{len(h.get('evidence') or [])}"
                        for h in BUZON["hypotheses"])


READ_ONLY_TOOLS = [
    "get_note", "get_note_concepts", "get_note_links", "get_note_backlinks",
    "get_related_notes", "get_related", "get_concept", "get_concept_full",
    "get_broader", "get_narrower", "get_top_concepts", "search_notes",
    "search_concepts", "search_with_dedup", "autocomplete_concepts", "list_notes",
    "get_collection", "get_collection_notes", "explore_graph", "get_full_document",
]


# ── Marcadores de validacion ─────────────────────────────────────────
# Generosos a proposito: rechazar de mas produce el mismo bloqueo que
# rechazar de menos, y el agente se queda sin registrar nada.

# Validacion por raices con regex, no por subcadenas exactas. La version anterior
# buscaba "en otros periodos" y rechazaba "en LOS otros periodos"; buscaba
# "encontraremos" y rechazaba "encontrare". Propuestas correctas se perdieron
# asi. Las marcas van sin tildes porque el texto se normaliza.

RE_MECANISMO = re.compile(
    r"\b(porque|puesto que|dado que|ya que|pues )"
    r"|\b(funciona|opera|actua|sirve|hace|obra|vale) (como|de|para)\b"
    r"|\bes (una|un) (forma|modo|manera|via|mecanismo|estrategia|intento)\b"
    r"|\bes consecuencia\b|\bse debe a\b|\bderiva de\b|\bresulta de\b|\bsurge de\b"
    r"|\bpor eso\b|\bpor ello\b|\bde ahi\b|\bhace que\b|\blleva a\b|\bconduce a\b"
    r"|\bprovoca\b|\bgenera\b|\bproduce\b|\bcausa\b|\bexplica\b|\bpermite\b"
    r"|\bevita\b|\bcompensa\b|\bsustituye\b|\bprotege\b|\bregula\b|\bblinda\b"
    r"|\bresponde a\b|\bobedece a\b|\bal precio de\b|\ba cambio de\b",
    re.IGNORECASE)

# Cuantas veces seguidas se ha rechazado por el mismo motivo. El validador es por
# regex, asi que mide si aparece la palabra, no si la prediccion arriesga de
# verdad: dos redacciones equivalentes pueden pasar una y fallar la otra. Una
# redaccion puede rechazarse varias veces por "prediction no arriesga" y pasar a
# la tercera con una frase del mismo sentido, quemando tokens por intento. A
# partir del tercer choque se deja de repetir el mismo reproche y se le da la
# frase que falta.
RECHAZOS_SEGUIDOS: dict = {}


def _rechazo(motivo: str, mensaje: str, remate: str = "") -> str:
    # La clave lleva el agente: si no, dos agentes distintos comparten contador y
    # uno recibe la pista por los intentos del otro. Se limpia al cerrar turno.
    clave = f"{CURRENT_AGENT.get('name', '?')}|{motivo}"
    n = RECHAZOS_SEGUIDOS.get(clave, 0) + 1
    RECHAZOS_SEGUIDOS[clave] = n
    if n < 3:
        return mensaje
    RECHAZOS_SEGUIDOS[clave] = 0
    return (mensaje + "\n\nEs la tercera vez seguida con lo mismo, asi que el problema "
            "puede ser la redaccion y no la idea. " + remate)


RE_PREDICCION = re.compile(
    r"\b(deber|esperar|encontrar|hallar|aparecer|registrar|observar|ver|surgir|predec)"
    r"(e|a|as|an|ia|ian|emos|amos|eremos|aremos|are|ara|aran|iamos)?\b"
    r"|\botros? period\w*\b|\bresto del corpus\b|\bmaterial (que no|nuevo|posterior|anterior)\b"
    r"|\bno he (mirado|revisado|visto|analizado)\b|\bsin (mirar|revisar|analizar)\b"
    r"|\bcabe esperar\b|\btendria que\b|\bdeberia\b"
    r"|\bsi se (analiza|examina|revisa|mira)\b|\ben adelante\b|\bmas tarde\b",
    re.IGNORECASE)

RE_FALSADOR = re.compile(
    r"\bse falsar\w*\b|\bfalsar\w* si\b|\bqueda\w* refutad\w*\b|\brefutad\w* si\b"
    r"|\bcae\w* si\b|\bcaeria\b|\bseria falsa\b|\bno se sostiene si\b"
    r"|\bbastar\w*\b|\bcontraejemplo\b|\bsi (aparec|ocurr|surg|hubier|existier|se encuentr)\w*"
    r"|\bquedaria (refutada|invalidada|sin)\b|\binvalidar\w* si\b|\bsi resulta\b",
    re.IGNORECASE)

MIN_CHARS = {"pattern": 40, "mechanism": 50, "prediction": 40, "falsifier": 40}


def _sin_tildes(texto: str) -> str:
    import unicodedata
    t = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _tiene(texto: str, patron) -> bool:
    return bool(patron.search(" ".join(_sin_tildes(texto).lower().split())))


def _solapa(a: str, b: str) -> float:
    """Fraccion de palabras de b que ya estan en a. Detecta mechanism = pattern."""
    wa = {w for w in re.findall(r"\w+", str(a).lower()) if len(w) > 3}
    wb = {w for w in re.findall(r"\w+", str(b).lower()) if len(w) > 3}
    if not wb:
        return 0.0
    return len(wa & wb) / len(wb)


PLANTILLA = (
    "\n\nPLANTILLA:\n"
    "  pattern    : 'Cuando <situacion observable>, el autor <respuesta observable>.'\n"
    "  mechanism  : '<respuesta> funciona como <funcion>, PORQUE <razon>.'\n"
    "  prediction : 'En otros periodos, <lo que deberia encontrarse si es cierta>.'\n"
    "  falsifier  : 'Se falsaria si <escenario concreto que la tumbaria>.'"
)


def _validar(pattern: str, mechanism: str, prediction: str, falsifier: str) -> str | None:
    """Devuelve el motivo del rechazo, o None si pasa."""
    campos = {"pattern": pattern, "mechanism": mechanism,
              "prediction": prediction, "falsifier": falsifier}
    vacios = [k for k, v in campos.items() if len(" ".join(str(v or "").split())) < MIN_CHARS[k]]
    if vacios:
        return (f"RECHAZADA: {', '.join(vacios)} vacio o demasiado corto. "
                f"Minimos: pattern {MIN_CHARS['pattern']}, mechanism {MIN_CHARS['mechanism']}, "
                f"prediction {MIN_CHARS['prediction']}, falsifier {MIN_CHARS['falsifier']} caracteres."
                + PLANTILLA)

    if not _tiene(mechanism, RE_MECANISMO):
        return _rechazo("mechanism",
            "RECHAZADA: mechanism no propone una causa, solo describe. Tiene que explicar "
            "POR QUE ocurre el patron, no repetirlo. Usa 'porque', 'funciona como', "
            "'sirve para', 'es consecuencia de'." + PLANTILLA,
            "No cambies la idea: escribe tu mismo mecanismo empezando por "
            "\"Ocurre porque...\".")

    if _solapa(pattern, mechanism) > 0.75:
        return ("RECHAZADA: mechanism repite el pattern con otras palabras. El pattern dice QUE "
                "pasa; el mechanism dice POR QUE. Si quitas el pattern, el mechanism tiene que "
                "seguir aportando algo." + PLANTILLA)

    if not _tiene(prediction, RE_PREDICCION):
        return _rechazo("prediction",
            "RECHAZADA: prediction no arriesga nada. Tiene que decir que esperarias encontrar "
            "en material que NO has mirado — otros periodos, otras notas. Si solo describe lo "
            "que ya viste, no es una prediccion." + PLANTILLA,
            "No cambies la idea: empieza la frase con \"En otros periodos deberia "
            "aparecer...\" y sigue con lo que ya tenias.")

    if not _tiene(falsifier, RE_FALSADOR):
        return ("RECHAZADA: falsifier no describe un escenario que la tumbe. Empieza por "
                "'Se falsaria si...' y di que hallazgo concreto la dejaria sin pie." + PLANTILLA)

    return None


def _buscar(hid: str) -> dict | None:
    h = str(hid or "").strip().upper()
    return next((x for x in BUZON["hypotheses"] if x["id"].upper() == h), None)


def _robusta(fn):
    """Envuelve una tool para que NINGUN fallo suyo llegue como traceback.

    Un fallo de una tool puede devolverle al agente un ToolError de cientos de
    caracteres: lo ve como un muro de traceback, reintenta a ciegas y por suerte
    acierta. La causa mas probable es que el modelo inventara un nombre de
    argumento y el despachador lanzara TypeError, pero es una conjetura.

    Sea cual sea la causa, el agente no puede hacer nada util con un traceback.
    Aqui cualquier excepcion se convierte en un mensaje que dice que tool fallo,
    con que argumentos se la llamo y cuales acepta. Si el fallo era un nombre de
    argumento mal puesto, con eso lo corrige al primer intento en vez de
    adivinar; y si era otra cosa, queda escrito en el log en vez de perderse.
    """
    import functools as _ft
    import inspect as _in
    import types as _ty

    # SOLO funciones. En la lista de tools hay tambien objetos que no lo son:
    # read_mcp es un MCPToolkit, un objeto con sus propias tools dentro.
    # Envolverlo con functools.wraps le pega un __wrapped__, y cuando ag2 le
    # pide la firma con inspect.signature la sigue hasta el toolkit y revienta:
    #   TypeError: descriptor '__call__' for 'type' objects doesn't apply to a
    #   'MCPToolkit' object
    # Eso puede tumbar la construccion del agente antes de empezar. Lo que
    # no es funcion pasa de largo sin tocarse.
    if not isinstance(fn, (_ty.FunctionType, _ty.MethodType, _ft.partial)):
        return fn

    @_ft.wraps(fn)
    def envuelta(*a, **k):
        try:
            return fn(*a, **k)
        except TypeError as e:
            firma = ", ".join(_in.signature(fn).parameters)
            return (f"RECHAZADO: llamaste a {fn.__name__} con argumentos que no acepta "
                    f"({e}).\n  Acepta exactamente: {firma}\n"
                    f"  Le pasaste: {', '.join(k) or '(posicionales)'}\n"
                    "Vuelve a llamarla con esos nombres. No cambies el contenido.")
        except Exception as e:
            _log_tool(CURRENT_AGENT.get("name", "?"), fn.__name__, str(k)[:200],
                      f"excepcion:{type(e).__name__}", 0.0)
            return (f"FALLO INTERNO en {fn.__name__}: {type(e).__name__}: {str(e)[:200]}\n"
                    "No es culpa de lo que escribiste. Reintenta una vez; si vuelve a "
                    "fallar, sigue con otra cosa y dilo en tu mensaje.")
    return envuelta


def _resumen_de(h: dict, tope: int = 150) -> str:
    """Una linea que diga de que va la hipotesis.

    Es el indice: lo que se muestra en la apertura y en list_hypotheses en vez
    del texto entero. Con muchas heredadas el texto completo ya ocupa una parte
    grande del prompt de sistema, y se repetia otra vez en cada llamada a
    list_hypotheses; con decenas no cabe.

    Lo escribe el agente al proponer, que es quien mejor sabe de que va. Si no
    lo escribio -- o si la hipotesis viene de un periodo anterior al campo -- se
    recorta el pattern por la primera frase.
    """
    r = " ".join(str(h.get("resumen") or "").split())
    if len(r) >= 12:
        # SIN TRUNCAR. El `tope` es para el fallback (recortar el pattern),
        # no para el resumen que escribio el agente. Truncarlo a 150 cortaba
        # palabras a medias: "el dese[cho]". El agente escribe dos o tres
        # frases a proposito; si se cortan, el indice miente sobre lo que
        # dice la hipotesis.
        return r
    texto = " ".join(str(h.get("pattern") or h.get("thesis") or "").split())
    if not texto:
        return "(sin resumen)"
    # Quitar las citas entrecomilladas: son ejemplos, no el meollo, y en un
    # recorte se comen la linea entera dejando fuera lo que la hipotesis dice.
    sin_citas = re.sub(r'\([^()]*[""\'"][^()]*\)', "", texto)
    sin_citas = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"\s{2,}", " ", sin_citas)).strip()
    texto = sin_citas if len(sin_citas) > 40 else texto
    if len(texto) <= tope:
        return texto
    corte = texto[:tope]
    for sep in (". ", "; ", ", "):
        pos = corte.rfind(sep)
        if pos > tope * 0.5:
            return corte[:pos].rstrip(" .,;") + "…"
    return corte[:corte.rfind(" ") if corte.rfind(" ") > 40 else tope].rstrip() + "…"


def _puede_actuar(h: dict) -> bool:
    """Quien puede defender, refinar o conceder esta hipotesis.

    La propia: siempre. Una HEREDADA de un periodo anterior: cualquiera de los
    dos agentes vivos.

    POR QUE. Una heredada llega con proposed_by="periodo_anterior", que no es un
    agente de esta corrida. Con la comprobacion estricta, atacarla la dejaba en
    EN_CURSO esperando a alguien que no existe: no se podia defender, ni
    refinar, ni conceder. Al cerrar caia a sin_responder por defecto. Un ataque
    a lo heredado era un callejon, nunca un ciclo.

    Adoptarla es lo correcto ademas por el fondo: una hipotesis heredada ya no
    es de nadie en particular, es del corpus. Quien la sostenga, que la sostenga.
    """
    return (h.get("proposed_by") == CURRENT_AGENT["name"]
            or bool(h.get("heredada"))
            or h.get("proposed_by") == "periodo_anterior")


def _evento(h: dict, tipo: str, **datos) -> None:
    h.setdefault("history", []).append({
        "period": PERIODO["label"], "event": tipo,
        "by": CURRENT_AGENT["name"],
        "ts": datetime.now(timezone.utc).isoformat(), **datos,
    })
    # Punto unico por el que pasa cualquier cambio de estado. Si una heredada
    # llega aqui, este periodo la toco: el parser la escribira. Si no llega
    # nunca, era solo contexto de lectura y no se re-materializa.
    h["tocada"] = True


def _notas_validas(txt: str) -> tuple[list, list]:
    """Extrae los note_ids por REGEX, no por separador.

    Antes hacia split(","). Un modelo escribia "id1,id2,id3" y funcionaba; otro
    los separo con espacios, y sin comas el split devolvia UNA sola cadena con
    los tres ids pegados, que no casa con el UUID. Rechazo: "ningun note_id
    utilizable". El agente, sin saber que era formato, fue soltando ids hasta
    pasar con uno solo: la hipotesis entro con 1 de 3 notas. Llamadas quemadas y
    evidencia mutilada.

    findall sobre el patron de UUID es inmune a coma, espacio, salto de linea o
    lo que se le ocurra al modelo.
    """
    ids = list(dict.fromkeys(UUID_RE.findall(str(txt or ""))))
    ok, ko = [], []
    for i in ids:
        v, motivo = _en_periodo(i)
        (ok if v else ko).append(i if v else f"{i} ({motivo})")
    return ok, ko


@tool
def propose_hypothesis(pattern: str, mechanism: str, prediction: str,
                       falsifier: str, evidence: str, asunto: str = "",
                       resumen: str = "") -> str:
    """Registrar una hipotesis nueva. Cinco piezas, todas obligatorias.

    No registras descripciones. Una descripcion ('el agotamiento aparece en el
    periodo') no se puede falsar: si esta en las notas, es verdad. Registras
    PROPOSICIONES: afirmaciones que podrian estar equivocadas.

    Antes de proponer, llama list_hypotheses. Si ya existe algo parecido, no crees
    una nueva: refuerzala con reinforce_hypothesis o desafiala. Las hipotesis
    crecen, no se multiplican.

    pattern:    QUE pasa. 'Cuando <situacion>, el autor <respuesta>.' Observable.
    mechanism:  POR QUE pasa. La causa o funcion que propones. Es la parte arriesgada.
    prediction: QUE esperarias encontrar en material que aun no has mirado.
    falsifier:  UN caso concreto que la tumbaria: "se falsaria si...". Es UN
                ejemplo que tu ofreces, no la lista de las unicas formas de
                atacarla. No estas acotando por donde te pueden venir: el otro
                agente puede tumbarla por donde encuentre, contra el mecanismo o
                contra la prediccion, aunque no se parezca a lo que escribas
                aqui. Sirve para que se vea que la hipotesis PUEDE caer, y para
                reconocer despues que dos hipotesis son la misma cuando el mismo
                hallazgo tumbaria a las dos.
    evidence:   note_ids completos del periodo, separados por coma.
    resumen: Dos o tres frases que expliquen la hipotesis ENTERA: que pasa
                (patron) y por que (mecanismo). No repitas el patron con otras
                palabras: si el mecanismo propone una causa, dila; si propone
                una funcion, dila. Es lo que otros agentes veran en su indice
                sin cargar la hipotesis completa, y lo primero que se lee al
                abrir la nota. Si no lo escribes se recorta el pattern, que
                casi siempre queda peor.
    asunto: OBLIGATORIO. De que va ESTA hipotesis, en dos a seis palabras y en
                espanol: "rutinas de arranque", "relacion con la
                escritura". Es el titulo con el que se va a leer. NO es una
                colección ni una carpeta: tu no decides donde se archiva esta
                hipotesis. Eso lo decide despues quien ve todas las hipotesis del
                periodo a la vez, porque desde aqui, metido en una sola, no se
                puede ver el campo del conjunto (trabajo, lectura, rutinas...).
                NO copies el nombre tecnico del libro de codigos
                (nombre_tecnico_de_ejemplo) ni un trozo literal del pattern.
    """
    fallo = _validar(pattern, mechanism, prediction, falsifier)
    if fallo:
        return fallo

    tema = " ".join(str(asunto or "").split())
    if len(tema) < 3:
        return ("RECHAZADA: falta asunto. Es de que va esta hipotesis, no una coleccion "
                "ni una carpeta -- eso se decide despues, viendo todas juntas.\n"
                "En ESPANOL y legible por una persona: \"rutinas de arranque\", \"relacion con la "
                "escritura\". NO copies el nombre tecnico del "
                "libro de codigos (nombre_tecnico_de_ejemplo) ni un trozo del pattern.\n"
                "Si el tema ya salio antes, usa exactamente el mismo nombre.")

    validos, rechazos = _notas_validas(evidence)
    if not validos:
        # El mensaje TIENE que decir cual fallo y por que. Un mensaje generico
        # hace que el agente no sepa si el problema era el formato o la evidencia:
        # se pone a reescribir el mechanism (que estaba bien) y acaba soltando
        # notas hasta pasar con una sola.
        if not rechazos:
            return ("RECHAZADA: no encontre ningun note_id en `evidence`. Un note_id "
                    "tiene esta forma: 00000000-0000-7000-8000-000000000000. "
                    "Puedes separarlos como quieras (coma, espacio, salto de linea).\n"
                    f"Lo que mandaste fue: {str(evidence)[:160]}")
        return ("RECHAZADA: encontre los note_id pero ninguno sirve para este periodo. "
                f"Periodo {PERIODO['label']}. Uno por uno:\n  "
                + "\n  ".join(rechazos[:6])
                + "\nNo es un problema del pattern ni del mechanism: es la evidencia. "
                  "Busca notas del periodo con notas_por_codigo y cita esas.")

    # POR EL MAYOR, NO POR LA CUENTA.
    # Antes: f"H-{len(BUZON['hypotheses']) + 1:03d}". Cuando los ids heredados no
    # son contiguos (algunos numeros faltan porque se falsaron), len()+1 podia dar
    # un id -- que YA EXISTIA. Dos hipotesis distintas con el mismo id, y _buscar
    # devuelve la primera: un desafio golpeaba a la heredada en vez de a la nueva.
    _mayor = 0
    for _h in BUZON["hypotheses"]:
        _m = re.fullmatch(r"[Hh]-(\d+)", str(_h.get("id", "")).strip())
        if _m:
            _mayor = max(_mayor, int(_m.group(1)))
    hid = f"H-{_mayor + 1:03d}"
    registro = {
        "id": hid,
        "asunto": tema,
        "resumen": _resumen_de({"resumen": resumen, "pattern": pattern}),
        "pattern": " ".join(str(pattern).split()),
        "mechanism": " ".join(str(mechanism).split()),
        "prediction": " ".join(str(prediction).split()),
        "falsifier": " ".join(str(falsifier).split()),
        "evidence": validos,
        "evidence_by_period": {PERIODO["label"]: validos},
        "origin_period": PERIODO["label"],
        "periods_tested": [PERIODO["label"]],
        "proposed_by": CURRENT_AGENT["name"],
        "status": "nuevo",
        "challenged_by": None,
        "counter_evidence": [],
        "challenge_reason": "",
        "defense": "",
        "history": [],
    }
    _evento(registro, "proposed", evidence=validos)
    BUZON["hypotheses"].append(registro)
    _persistir("hypothesis", registro)

    aviso = f" Descartados: {'; '.join(rechazos[:4])}." if rechazos else ""
    return (f"Registrada {hid} (NUEVO) con {len(validos)} notas.{aviso} "
            "Sigue sin estar probada: no cuenta hasta que alguien la desafie. "
            "Dilo en tu mensaje para que el otro la ataque.")


@tool
def challenge_hypothesis(hypothesis_id: str, counter_evidence: str, reason: str,
                         cumple_falsifier: bool = False, duplicate_of: str = "",
                         falsador_comun: str = "") -> str:
    """Atacar una hipotesis con evidencia que la contradice.

    ATACA POR DONDE QUIERAS. El falsifier que escribio el proponente es UN caso
    que el ofrecio, no la lista de puertas permitidas: si encuentras material que
    contradice su mecanismo o su prediccion, eso la tumba igual aunque no se
    parezca en nada a lo que el declaro. La falsacion mas fuerte suele caer por
    la prediccion, no por el falsifier formal.

    Un buen ataque va contra el MECHANISM o cumple el FALSIFIER, no contra el
    pattern. Que el patron exista no suele estar en duda; lo que se discute es la
    causa que se le atribuye y si la prediccion se sostiene.

    hypothesis_id:    H-NNN.
    counter_evidence: note_ids del periodo que la contradicen, separados por coma.
    reason:           por que esa evidencia la contradice. Una o dos frases.
    cumple_falsifier: True si tu evidencia realiza el escenario que la propia hipotesis
                      declaro como su falsador. Es el ataque mas fuerte que existe: el
                      autor escribio que caeria si pasaba X, y tu traes X. Al marcarlo,
                      la carga pasa a su autor: si no rebate el falsador, la hipotesis
                      cae al cerrar el debate. Marcalo solo si de verdad se cumple.
    falsador_comun:   Un hallazgo concreto que tumbaria a las DOS (si tienes uno).
                      Pero el criterio de si son la misma NO es solo el falsador:
                      mira las 5 piezas (patron, mecanismo, prediccion, falsador,
                      evidencia). Dos hipotesis pueden ser el mismo hallazgo aunque
                      tengan falsadores distintos, si son el mismo fenomeno visto
                      desde angulos distintos. SESGO A NO DUPLICAR: si tienes duda,
                      no las marques duplicadas. Compartir coleccion no implica
                      ser la misma hipotesis.
    duplicate_of:     H-NNN de la que esta ya dice lo mismo. No decides tu la fusion:
                      dejas la señal y el parser la ejecuta, absorbiendo esta en aquella
                      sin perder su evidencia ni su historia.
    """
    h = _buscar(hypothesis_id)
    if not h:
        return f"RECHAZADO: no existe {hypothesis_id}. Mira list_hypotheses."
    if h["status"] == "falsado":
        return f"{h['id']} ya esta falsada. Busca otra."
    if h.get("duplicate_of"):
        return (f"{h['id']} ya esta marcada como duplicado de {h['duplicate_of']}. "
                f"Si quieres atacar el contenido, ataca a {h['duplicate_of']}.")
    if h.get("proposed_by") == CURRENT_AGENT["name"]:
        return (f"RECHAZADO: {h['id']} la propusiste tu. No puedes desafiar tu propia "
                "hipotesis. Para corregirla, usa refine_hypothesis.")

    validos, rechazos = _notas_validas(counter_evidence)
    if not validos:
        return ("RECHAZADO: ningun note_id valido. " + "; ".join(rechazos[:3]) +
                f" Deben ser del periodo {PERIODO['label']} y completos.")
    if len(str(reason).split()) < 8:
        return "RECHAZADO: reason demasiado corto. Explica por que esa evidencia la contradice."

    solo_repite = set(validos) <= set(h.get("evidence") or [])
    falsif = bool(cumple_falsifier) and bool(h.get("falsifier"))

    dup = str(duplicate_of or "").strip().upper()
    aviso_dup = ""
    if dup:
        canon = _buscar(dup)
        if not canon:
            aviso_dup = f"\nAVISO: {dup} no existe, la señal de duplicado se ignora."
            dup = ""
        elif canon["id"].upper() == h["id"].upper():
            aviso_dup = "\nAVISO: no puede ser duplicado de si misma."
            dup = ""
        elif len(" ".join(str(falsador_comun or "").split()).split()) < 8:
            aviso_dup = ("\nSEÑAL DE DUPLICADO IGNORADA: falta falsador_comun. Para marcar "
                         f"{h['id']} como duplicada de {canon['id']} tienes que decir que "
                         "hallazgo tumbaria a las dos. Ese es el criterio.\n"
                         f"  Falsador de {h['id']}: {h.get('falsifier','')}\n"
                         f"  Falsador de {canon['id']}: {canon.get('falsifier','')}\n"
                         "Si no comparten falsador, no son duplicadas: son dos angulos del "
                         "mismo tema y comparten coleccion, no identidad. El desafio queda "
                         "registrado igual.")
            dup = ""
        else:
            h["duplicate_of"] = canon["id"]
            h["falsador_comun"] = " ".join(str(falsador_comun).split())
            aviso_dup = (f"\nDUPLICADO señalado: {h['id']} -> {canon['id']}. El parser la "
                         "absorbera: su evidencia y su historia pasan a la canonica y esta "
                         "no se escribe como hipotesis aparte. No hace falta que la fusiones tu.")
    h["status"] = "en_curso"
    h["challenged_by"] = CURRENT_AGENT["name"]
    h["counter_evidence"] = validos
    h["challenge_reason"] = " ".join(str(reason).split())
    h["challenge_cumple_falsifier"] = falsif
    if falsif:
        h["status"] = "falsador_invocado"
    _evento(h, "challenged", counter_evidence=validos, reason=h["challenge_reason"],
            reinterpretacion=solo_repite, cumple_falsifier=falsif, duplicate_of=dup or None)
    _persistir("challenge", {"hypothesis_id": h["id"], "challenger": CURRENT_AGENT["name"],
                             "counter_evidence": validos, "reason": h["challenge_reason"]})

    fuerte = ("\nFALSADOR INVOCADO. Estado: FALSADOR_INVOCADO, no en_curso. La carga de la "
              "prueba pasa a su autor: tiene que rebatir el falsador explicitamente en su "
              "defensa, o refinarlo y justificar el cambio. SI NO RESPONDE, LA HIPOTESIS CAE "
              "AL CERRAR EL DEBATE.\n"
              f"  Falsador declarado: {h.get('falsifier', '')}" if falsif else "")
    nota = ("\nAVISO: tu contra-evidencia son las MISMAS notas que la respaldan. Eso no es "
            "contra-evidencia, es una lectura distinta del mismo material. Vale, pero es un "
            "ataque debil: busca notas que la hipotesis no explique." if solo_repite else "")
    quien = ("cualquiera de los dos (viene de otro periodo, no tiene dueño aqui)"
             if h.get("heredada") or h.get("proposed_by") == "periodo_anterior"
             else h["proposed_by"])
    return (f"{h['id']} desafiada. Estado: EN_CURSO. {quien} debe responder con "
            f"defend_hypothesis, refine_hypothesis o concede_hypothesis."
            + fuerte + nota + aviso_dup)


@tool
def defend_hypothesis(hypothesis_id: str, defense: str, extra_evidence: str = "",
                      rebate_falsifier: str = "") -> str:
    """Defender tu hipotesis de un desafio sin cambiarla.

    Usala cuando el ataque no la tumba: explica por que la contra-evidencia no
    aplica, o por que es compatible con tu mecanismo.

    hypothesis_id:  H-NNN, tuya.
    defense:        por que el desafio no la tumba. Una o dos frases.
    extra_evidence:   opcional, note_ids nuevos que refuercen tu posicion.
    rebate_falsifier: OBLIGATORIO si te atacaron invocando tu falsador. Explica por que
                      el escenario que declaraste NO se cumple de verdad en esa evidencia.
                      Si no puedes rebatirlo, no defiendas: concede o refina el falsador.
    """
    h = _buscar(hypothesis_id)
    if not h:
        return f"RECHAZADO: no existe {hypothesis_id}."
    if not _puede_actuar(h):
        return f"RECHAZADO: {h['id']} no es tuya. La propuso {h.get('proposed_by')}."
    if h["status"] not in ("en_curso", "falsador_invocado"):
        return (f"RECHAZADO: {h['id']} no esta desafiada (estado {h['status'].upper()}). "
                "Solo se defiende lo que fue atacado.")
    if h["status"] == "falsador_invocado" and len(str(rebate_falsifier).split()) < 12:
        return ("RECHAZADO: te atacaron invocando TU PROPIO falsador, asi que la defensa "
                "tiene que rebatirlo. Rellena rebate_falsifier explicando por que el "
                "escenario que declaraste no se cumple en esa evidencia.\n"
                f"  Tu falsador: {h.get('falsifier', '')}\n"
                f"  Su ataque  : {h.get('challenge_reason', '')}\n"
                "Si no puedes rebatirlo, usa concede_hypothesis o refine_hypothesis.")
    if len(str(defense).split()) < 10:
        return "RECHAZADO: defense demasiado corta. Explica por que el ataque no la tumba."

    nuevos, _ = _notas_validas(extra_evidence)
    if nuevos:
        h["evidence"] = list(dict.fromkeys(h["evidence"] + nuevos))
        h["evidence_by_period"].setdefault(PERIODO["label"], [])
        h["evidence_by_period"][PERIODO["label"]] = list(dict.fromkeys(
            h["evidence_by_period"][PERIODO["label"]] + nuevos))

    era_critico = h["status"] == "falsador_invocado"
    h["defense"] = " ".join(str(defense).split())
    if rebate_falsifier:
        h["rebate_falsifier"] = " ".join(str(rebate_falsifier).split())
    h["status"] = "defendida"
    _evento(h, "defended", defense=h["defense"], extra_evidence=nuevos,
            rebate_falsifier=h.get("rebate_falsifier", ""), rebatio_falsador=era_critico)
    _persistir("defense", {"hypothesis_id": h["id"], "defense": h["defense"],
                           "extra_evidence": nuevos,
                           "rebate_falsifier": h.get("rebate_falsifier", "")})
    extra = f" Añadidas {len(nuevos)} notas." if nuevos else ""
    critico = (" Y sobrevivio a una invocacion de su propio falsador, que es el ataque mas "
               "fuerte posible." if era_critico else "")
    return (f"{h['id']} defendida. Estado: DEFENDIDA — sobrevivio a un desafio con "
            f"evidencia.{extra}{critico} Al cerrar el debate pasara a ACTIVO.")


@tool
def refine_hypothesis(hypothesis_id: str, mechanism: str = "", pattern: str = "",
                      prediction: str = "", falsifier: str = "", reason: str = "") -> str:
    """Reescribir tu hipotesis incorporando lo que el desafio acerto.

    Es lo que hay que hacer cuando el ataque tiene parte de razon pero no la tumba
    entera. Rellena solo los campos que cambian. Queda registrado el texto anterior.

    hypothesis_id: H-NNN, tuya.
    reason:        que del desafio incorporas y por que. Obligatorio.
    """
    h = _buscar(hypothesis_id)
    if not h:
        return f"RECHAZADO: no existe {hypothesis_id}."
    if not _puede_actuar(h):
        return f"RECHAZADO: {h['id']} no es tuya."
    cambios = {k: " ".join(str(v).split()) for k, v in
               (("pattern", pattern), ("mechanism", mechanism),
                ("prediction", prediction), ("falsifier", falsifier)) if str(v).strip()}
    if not cambios:
        return "RECHAZADO: no indicaste ningun campo a cambiar."
    if len(str(reason).split()) < 8:
        return "RECHAZADO: explica en reason que del desafio estas incorporando."

    prop = {**{k: h[k] for k in ("pattern", "mechanism", "prediction", "falsifier")}, **cambios}
    fallo = _validar(prop["pattern"], prop["mechanism"], prop["prediction"], prop["falsifier"])
    if fallo:
        return fallo.replace("RECHAZADA:", "RECHAZADO (la version refinada no valida):")

    anterior = {k: h[k] for k in cambios}
    era_critico = h["status"] == "falsador_invocado"
    h.update(cambios)
    h["status"] = "refinada"
    _evento(h, "refined", changed=list(cambios), previous=anterior,
            reason=" ".join(str(reason).split()), tras_falsador=era_critico)
    # Se guardan tambien los VALORES nuevos, no solo que campos cambiaron: sin
    # ellos, el replay al reanudar sabria que hubo un refine pero no como quedo.
    _persistir("refine", {"hypothesis_id": h["id"], "changed": list(cambios),
                          "previous": anterior, "reason": reason, **cambios})
    return (f"{h['id']} refinada ({', '.join(cambios)}). Estado: REFINADA. "
            "La version anterior queda en su historial. Puede volver a ser desafiada.")


@tool
def concede_hypothesis(hypothesis_id: str, reason: str = "") -> str:
    """Aceptar que tu hipotesis cayo. Pasa a FALSADA.

    Conceder no es perder: una hipotesis falsada SI se escribe en Fortemi, con su
    trazo completo -- que cayo, por que, y con que evidencia. Es un resultado
    tan valido como uno que sobrevive, y queda ahi para que se pueda reabrir si
    material futuro la sostiene. Insistir en algo refutado si es perder.
    """
    h = _buscar(hypothesis_id)
    if not h:
        return f"RECHAZADO: no existe {hypothesis_id}."
    if not _puede_actuar(h):
        return (f"RECHAZADO: {h['id']} no es tuya. Solo la concede quien la propuso "
                "(o cualquiera de los dos, si viene de un periodo anterior).")

    # Una evidencia mala mata la evidencia, no la hipotesis. Para conceder hay
    # que decir que cayo: el mecanismo o la prediccion. Si el motivo solo habla
    # de una nota mal citada, la herramienta correcta es refine_hypothesis.
    motivo = " ".join(str(reason or "").split())
    # _tiene() llama .search() sobre el patron: tienen que ir compiladas, como
    # RE_MECANISMO y las demas. Con cadenas crudas esto reventaba con
    # AttributeError: 'str' object has no attribute 'search' — y como pasaba
    # dentro de concede_hypothesis, mataba el turno del agente y el canal se
    # quedaba esperando hasta que saltaba el detector de parada.
    _MECA = re.compile(r"mecanis|explicaci|causa|porque|por que|funcion", re.I)
    _PRED = re.compile(r"predicc|prediga|esperab|anticipab|no aparece|no se cumple|no ocurre", re.I)
    _EVID = re.compile(r"evidenci|nota|cita|note_id|id citado|tracklist|checklist", re.I)
    if len(motivo.split()) < 12:
        return ("RECHAZADO: conceder exige un motivo de al menos 12 palabras que diga "
                "QUE cayo: el mecanismo o la prediccion.\n"
                "Plantilla: \"Concedo: el mecanismo no se sostiene porque <...>\" o "
                "\"Concedo: la prediccion no se cumple, en <material> aparece <...>\".")
    if not _tiene(motivo, _MECA) and not _tiene(motivo, _PRED):
        if _tiene(motivo, _EVID):
            return ("RECHAZADO: eso no es conceder, es perder una evidencia.\n"
                    f"Una nota mal citada no tumba {h['id']}: tumba esa nota. La hipotesis "
                    "cae cuando su MECANISMO no se sostiene o su PREDICCION no se cumple.\n"
                    "Usa refine_hypothesis para soltar la evidencia mala y dejar la hipotesis "
                    "en pie sobre el resto. Si de verdad se queda sin ninguna evidencia, "
                    "dilo asi y entonces si concede.")
        return ("RECHAZADO: el motivo no dice que cayo. Nombra el mecanismo o la prediccion.\n"
                "Plantilla: \"Concedo: el mecanismo no se sostiene porque <...>\".")

    h["status"] = "falsado"
    _evento(h, "conceded", reason=" ".join(str(reason).split()))
    _persistir("concede", {"hypothesis_id": h["id"], "reason": reason})
    return (f"{h['id']} FALSADA. Queda en el registro como hipotesis refutada, "
            "con la contra-evidencia que la tumbo.")


@tool
def reinforce_hypothesis(hypothesis_id: str, new_evidence: str, note: str) -> str:
    """Alimentar una hipotesis de un periodo anterior con evidencia de este.

    Si la prediccion de una hipotesis previa se cumple en el material de este
    periodo, no crees una hipotesis nueva: refuerza la que ya existe. Asi la
    hipotesis crece y acumula historia en vez de multiplicarse.

    hypothesis_id: H-NNN, tuya o de otro, de este periodo o de uno anterior.
    new_evidence:  note_ids de ESTE periodo que la sostienen.
    note:          como se cumple aqui la prediccion. Una o dos frases.
    """
    h = _buscar(hypothesis_id)
    if not h:
        return f"RECHAZADO: no existe {hypothesis_id}. Mira list_hypotheses."
    if h["status"] == "falsado":
        return f"RECHAZADO: {h['id']} esta falsada. No se refuerza lo refutado."
    validos, rechazos = _notas_validas(new_evidence)
    if not validos:
        return ("RECHAZADO: ningun note_id valido de este periodo. " + "; ".join(rechazos[:3]))
    if len(str(note).split()) < 8:
        return "RECHAZADO: explica en note como se cumple aqui la prediccion."

    p = PERIODO["label"]
    h["evidence"] = list(dict.fromkeys(h.get("evidence", []) + validos))
    h.setdefault("evidence_by_period", {}).setdefault(p, [])
    h["evidence_by_period"][p] = list(dict.fromkeys(h["evidence_by_period"][p] + validos))
    if p not in h.setdefault("periods_tested", []):
        h["periods_tested"].append(p)
    _evento(h, "reinforced", evidence=validos, note=" ".join(str(note).split()))
    _persistir("reinforce", {"hypothesis_id": h["id"], "period": p,
                             "evidence": validos, "note": note})
    return (f"{h['id']} reforzada con {len(validos)} notas de {p}. "
            f"Probada ya en: {', '.join(h['periods_tested'])}. "
            "Su prediccion se cumplio aqui, lo que la hace mas dificil de tumbar.")


@tool
def emit_collection(name: str, hypothesis_ids: str, note_ids: str,
                    description: str = "") -> str:
    """Señalar que unas notas del corpus pertenecen al mismo asunto que unas hipótesis,
    aunque ninguna las cite como evidencia.

    ESTO NO CREA NINGUNA CARPETA. Es una señal, y es la única que nadie más puede dar:
    las notas que una hipótesis CITA ya se archivan solas, porque están en su evidencia.
    Lo que solo tú puedes decir es "esta otra nota también va aquí aunque no la usé como
    prueba". Eso es lo que registra esta herramienta.

    Dónde acaba archivado todo eso lo decide después quien ve todas las hipótesis del
    periodo a la vez. Desde aquí, dentro de una sola hipótesis, el tema del conjunto no
    se ve.

    CUÁNDO: en cuanto una hipótesis pase a DEFENDIDA o REFINADA. No esperes al final —
    si se acaba el presupuesto antes, la señal se pierde.

    name:            el asunto que comparten, en tus palabras.
    hypothesis_ids:  H-NNN separados por coma. No entran las falsadas.
    note_ids:        notas del corpus que van con ellas SIN ser evidencia de ninguna.
    description:     opcional, una frase sobre qué las une.
    """
    nombre = " ".join(str(name).split())
    if not nombre:
        return "RECHAZADA: la colección necesita nombre."

    hids = [x.strip().upper() for x in str(hypothesis_ids).split(",") if x.strip()]
    nids = [x.strip() for x in str(note_ids).split(",")
            if x.strip() and _en_periodo(x.strip())[0]]
    vivas = {h["id"].upper() for h in BUZON["hypotheses"] if h["status"] != "falsado"}
    descartadas = [h for h in hids if h not in vivas]
    hids = [h for h in hids if h in vivas]
    if not hids and not nids:
        return ("RECHAZADA: sin hipótesis vivas ni note_ids válidos del periodo."
                + (f" No existen o están falsadas: {', '.join(descartadas[:4])}." if descartadas else ""))

    existente = next((c for c in BUZON["collections"]
                      if _norm(c["name"]) == _norm(nombre)), None)
    if existente:
        antes = len(existente["notes"])
        existente["notes"] = list(dict.fromkeys(existente["notes"] + nids + hids))
        if description and not existente.get("description"):
            existente["description"] = " ".join(str(description).split())
        _persistir("collection", existente)
        nuevos = len(existente["notes"]) - antes
        return (f"Colección '{existente['name']}' ampliada: {nuevos} elementos nuevos, "
                f"{len(existente['notes'])} en total.")

    registro = {
        "name": nombre,
        "description": " ".join(str(description).split()),
        "create_if_not_exists": True,
        "notes": list(dict.fromkeys(nids + hids)),
        "period": PERIODO["label"],
    }
    BUZON["collections"].append(registro)
    _persistir("collection", registro)
    aviso = (f" Descartadas por falsadas o inexistentes: {', '.join(descartadas[:4])}."
             if descartadas else "")
    return (f"Colección '{nombre}' creada con {len(nids)} notas y {len(hids)} hipótesis.{aviso} "
            "Vuelve a llamarla con el mismo nombre para añadirle cosas.")


@tool
def notas_por_codigo(code: str, max_notas: int = 12) -> str:
    """Qué notas del periodo llevan un código del libro de códigos.

    Es la forma correcta de pasar de una categoría a la evidencia. NO uses búsqueda
    semántica para esto: el corpus tiene notas de otros periodos y te las va a
    devolver. Este índice sale del análisis de ESTE periodo, así que todo lo que
    devuelve es material válido.

    code: nombre del código. Admite coincidencia parcial: 'conflict' devuelve todos los que contengan eso.

    Cada línea trae [cita] o [segmento]. [cita] es una frase textual verificada
    contra la nota. [segmento] es el inicio del fragmento analizado, porque una
    frase propuesta por el análisis previo no estaba en el texto y se descartó.
    Si vas a entrecomillar algo, usa solo [cita], o abre la nota con get_note.
    """
    if not INDICE_CODIGOS:
        return "El índice de códigos no está disponible en esta corrida."
    q = str(code).strip()
    if not q:
        return "Indica un código. Usa listar_codigos() para ver todos los disponibles."

    qn = _norm(q)
    claves = list(INDICE_CODIGOS)

    # 1. Exacto normalizado: ignora mayúsculas, guiones, espacios y acentos raros
    parciales = [c for c in claves if _norm(c) == qn]
    # 2. Subcadena en cualquier dirección: 'conflict' o 'state_tension'
    if not parciales:
        parciales = [c for c in claves if qn in _norm(c) or _norm(c) in qn]
    # 3. Solapamiento de palabras: 'conflict' encuentra 'recurring_state_conflict'
    if not parciales:
        tq = _tokens(q)
        if tq:
            puntuadas = []
            for c in claves:
                comunes = tq & _tokens(c)
                if comunes:
                    puntuadas.append((len(comunes), len(INDICE_CODIGOS[c]), c))
            puntuadas.sort(reverse=True)
            parciales = [c for _, _, c in puntuadas[:6]]

    if not parciales:
        top = sorted((c for c in claves if c in CATEGORIAS_INDEXADAS),
                     key=lambda c: -len(INDICE_CODIGOS[c]))[:15] or \
              sorted(claves, key=lambda c: -len(INDICE_CODIGOS[c]))[:15]
        muestra = ", ".join(f"{c}({len(INDICE_CODIGOS[c])})" for c in top)
        return (f"Ningún código coincide con '{code}'. NO sigas adivinando nombres: "
                f"llama a listar_codigos() una vez y trabaja con esa lista.\n"
                f"Los que más notas tienen: {muestra}")

    lineas, total = [], 0
    parciales.sort(key=lambda x: (x not in CATEGORIAS_INDEXADAS, -len(INDICE_CODIGOS[x])))
    for c in parciales[:8]:
        entradas = INDICE_CODIGOS[c]
        total += len(entradas)
        lineas.append(f"{c} ({len(entradas)} notas):")
        for e in entradas[:max_notas]:
            marca = "cita" if e.get("literal") else "segmento"
            lineas.append(f"  {e['note_id']}  [{marca}] \"{e['frase']}\"")
        if len(entradas) > max_notas:
            lineas.append(f"  ... y {len(entradas) - max_notas} notas más")
    if total == 0:
        return f"'{code}' existe como clave pero ninguna nota lo lleva en este periodo."
    return "\n".join(lineas)


@tool
def buscar_hipotesis_fortemi(consulta: str = "", limite: int = 20) -> str:
    """Buscar hipotesis ya registradas EN FORTEMI, de corridas anteriores.

    Fortemi es el indice permanente: guarda las hipotesis que sobrevivieron debates
    de otros periodos, etiquetadas con el tag 'thesis' y su H-NNN. list_hypotheses
    solo ve las de ESTE proceso; esta ve las de todos.

    NO sustituye a list_hypotheses: son dos memorias distintas y hay que mirar las dos.
    list_hypotheses tiene lo de ESTE debate (incluido lo que registraste hace un turno);
    esta tiene lo de periodos ANTERIORES. Una idea puede estar cubierta en cualquiera.

    Si alguna cubre tu idea: no crees una nueva. Refuerzala con reinforce_hypothesis
    usando notas de este periodo, o desafiala.
    Si ninguna la cubre: es genuinamente nueva y DEBES proponerla. El objetivo es que
    las hipotesis no se dupliquen, no que dejes de registrar por prudencia.

    consulta: palabras clave. Vacio devuelve las mas recientes.
    """
    rest = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000").rstrip("/")
    cab = {"Authorization": f"Bearer {REST_TOKEN['value']}"}
    lim = max(1, min(int(limite or 20), 50))
    try:
        r = httpx.get(f"{rest}/api/v1/notes",
                      params={"tags": "thesis", "limit": lim}, headers=cab, timeout=30.0)
        if r.status_code != 200:
            return (f"No se pudo consultar el indice de Fortemi (HTTP {r.status_code}). "
                    "Sigue con list_hypotheses (las de este debate) y, si nada cubre tu "
                    "idea, propon: no dejes de registrar por un fallo de red.")
        data = r.json()
        notas = data.get("notes") if isinstance(data, dict) else data
    except Exception as e:
        return (f"Fallo al consultar Fortemi: {str(e)[:140]}. Sigue con list_hypotheses "
                "y propon igualmente si nada cubre tu idea.")

    if not notas:
        return ("No hay hipotesis en Fortemi todavia: este es el primer periodo analizado, "
                "o el parser aun no ha escrito nada. Todo lo que propongas aqui es nuevo "
                "por definicion. Comprueba solo list_hypotheses y adelante.")

    # TRES BUGS QUE HABIA AQUI, y por los que esta tool era decorativa:
    #
    # 1. El filtro era `q not in _norm(texto)`: substring EXACTO. Una consulta de
    #    dos palabras solo casaba si esa frase literal estaba en la nota. Un
    #    agente puede llamarla varias veces y recibir siempre "ninguna coincide",
    #    sin llegar a ver nunca las heredadas por esta via.
    # 2. `re.match(r"^H-\d+$")` es sensible a mayusculas y Fortemi devuelve los
    #    tags en MINUSCULA ("h-001"), asi que el numero salia siempre "H-?".
    # 3. `n.get("content")`: la vista de LISTA de GET /notes no trae content. Se
    #    filtraba sobre una cadena vacia.
    #
    # Ahora: se piden los contenidos de verdad, el numero se lee sin importar
    # mayusculas, y el filtro puntua por palabras compartidas en vez de exigir
    # la frase entera.
    def _hid_de(nota) -> str:
        for t in (nota.get("tags") or []):
            nombre = t.get("name", t) if isinstance(t, dict) else t
            if re.fullmatch(r"[Hh]-\d{3,}", str(nombre).strip()):
                return str(nombre).strip().upper()
        return "H-?"

    def _texto_de(nota) -> str:
        cuerpo = str(nota.get("content") or "")
        if cuerpo:
            return cuerpo
        try:
            rr = httpx.get(f"{rest}/api/v1/notes/{nota.get('id')}", headers=cab, timeout=20.0)
            if rr.status_code == 200:
                d = rr.json() or {}
                return str((d.get("original") or {}).get("content")
                           or (d.get("revised") or {}).get("content") or "")
        except Exception:
            pass
        return str(nota.get("title") or "")

    palabras_q = {w for w in re.findall(r"\w{4,}", _norm(consulta)) if w} if consulta else set()
    puntuadas = []
    for n in notas:
        texto = _texto_de(n)
        if palabras_q:
            palabras_t = set(re.findall(r"\w{4,}", _norm(texto)))
            comunes = len(palabras_q & palabras_t)
            if not comunes:
                continue
            punt = comunes / len(palabras_q)
        else:
            punt = 0.0
        puntuadas.append((punt, _hid_de(n), n.get("id"), " ".join(texto.split())[:300]))
    puntuadas.sort(key=lambda x: -x[0])
    filas = [f"{hid}  (nota {nid})" + (f"  [coincide {punt:.0%}]" if punt else "")
             + f"\n  {resumen}..."
             for punt, hid, nid, resumen in puntuadas[:lim]]

    if not filas:
        return (f"Ninguna de las {len(notas)} hipotesis de Fortemi coincide con '{consulta}'. "
                "Prueba sin consulta para verlas todas. Si aun asi ninguna cubre tu idea, "
                "es genuinamente nueva: proponla.")
    return (f"{len(filas)} hipotesis en el indice de Fortemi"
            + (f" que coinciden con '{consulta}'" if consulta else "") + ":\n\n"
            + "\n\n".join(filas)
            + "\n\nSi alguna cubre lo que ibas a proponer, refuerzala o desafiala en vez "
              "de crear una nueva.")


@tool
def listar_codigos(minimo_notas: int = 2, incluir_crudos: bool = False) -> str:
    """Los nombres de código que puedes consultar, con cuántas notas tiene cada uno.

    Llámala UNA VEZ al empezar tu primer turno. Los nombres del libro de códigos no
    siempre coinciden con los que acepta el índice: esta lista es la verdad.

    Devuelve primero las CATEGORÍAS del libro (las que significan algo). Los códigos
    crudos del análisis son cientos y casi todos tienen una sola nota; solo salen si
    pides incluir_crudos=True.

    minimo_notas: filtra los que tengan menos notas que esto. Baja a 1 si buscas algo
                  concreto que no aparece.
    """
    if not INDICE_CODIGOS:
        return "El índice de códigos no está disponible en esta corrida."
    umbral = max(1, int(minimo_notas or 1))

    cats = [(len(v), k) for k, v in INDICE_CODIGOS.items()
            if k in CATEGORIAS_INDEXADAS and len(v) >= umbral]
    crudos = [(len(v), k) for k, v in INDICE_CODIGOS.items()
              if k not in CATEGORIAS_INDEXADAS and len(v) >= umbral]
    cats.sort(key=lambda x: (-x[0], x[1]))
    crudos.sort(key=lambda x: (-x[0], x[1]))

    total_notas = len({e["note_id"] for v in INDICE_CODIGOS.values() for e in v})
    out = [f"CATEGORÍAS DEL LIBRO con >= {umbral} notas ({len(cats)} de "
           f"{len(CATEGORIAS_INDEXADAS)}), sobre {total_notas} notas del periodo:"]
    out += [f"  {n:>3}  {k}" for n, k in cats]

    if incluir_crudos:
        presupuesto, filas = 6000, []
        for n, k in crudos:
            linea = f"  {n:>3}  {k}"
            if presupuesto - len(linea) < 0:
                filas.append(f"  ... y {len(crudos) - len(filas)} códigos crudos más")
                break
            filas.append(linea)
            presupuesto -= len(linea)
        out += ["", f"CÓDIGOS CRUDOS con >= {umbral} notas ({len(crudos)}):"] + filas
    else:
        bajos = sum(1 for k, v in INDICE_CODIGOS.items()
                    if k not in CATEGORIAS_INDEXADAS and len(v) < umbral)
        out += ["", f"Hay además {len(crudos) + bajos} códigos crudos del análisis, casi todos "
                    "con una sola nota. Pide incluir_crudos=True solo si las categorías "
                    "no te bastan."]
    return "\n".join(out)


@tool
def list_hypotheses(solo_activas: bool = False, id: str = "") -> str:
    """El índice de hipótesis: una línea por cada una.

    Llámala ANTES de proponer nada. Si ya existe algo parecido a lo que ibas a
    decir, no crees una nueva: refuérzala si la sostienes o desafíala si no.
    Las hipótesis crecen, no se multiplican.

    También es tu memoria fiable: lo registrado está en disco aunque tú hayas
    perdido el hilo de la conversación.

    id: para ver UNA entera — pattern, mecanismo, predicción, falsador,
        evidencia e historia. Por ejemplo id="H-003". Sin esto devuelve el
        índice, que es lo que necesitas para decidir cuál abrir. Con la
        herencia creciendo cada periodo, pedirlas todas enteras no cabe.
    """
    yo = CURRENT_AGENT["name"]
    pendientes = [h for h in BUZON["hypotheses"]
                  if h.get("proposed_by") == yo
                  and h["status"] in ("en_curso", "falsador_invocado")]
    sin_atacar = [h for h in BUZON["hypotheses"]
                  if h.get("proposed_by") not in (yo, None)
                  and h["status"] == "nuevo" and not h.get("duplicate_of")]

    cabecera = []
    if pendientes:
        cabecera.append("⚠ TIENES DESAFÍOS SIN RESPONDER. Resuélvelos ANTES de proponer nada:")
        for h in pendientes:
            urg = " [FALSADOR INVOCADO: si no lo rebates, CAE al cerrar]" \
                if h["status"] == "falsador_invocado" else ""
            cabecera.append(f"   {h['id']}{urg} — atacada por {h.get('challenged_by')}: "
                            f"{h.get('challenge_reason', '')[:150]}")
        cabecera.append("   Responde con defend_hypothesis, refine_hypothesis o "
                        "concede_hypothesis. Una hipótesis tuya que quede sin respuesta no "
                        "llega a Fortemi: es trabajo perdido.")
        cabecera.append("")
    if sin_atacar:
        cabecera.append(f"→ {len(sin_atacar)} hipótesis del otro agente siguen SIN DEBATE "
                        f"({', '.join(h['id'] for h in sin_atacar)}). Sin tu ataque no cuentan. "
                        "Atácalas o dilo si te convencen.")
        cabecera.append("")

    hs = BUZON["hypotheses"]
    if solo_activas:
        hs = [h for h in hs if h["status"] != "falsado"]
    if not hs and not BUZON["collections"]:
        return ("Todavía no hay nada registrado. Puedes ser el primero: propón con "
                "propose_hypothesis (pattern, mechanism, prediction, falsifier, evidence).")

    etiquetas = {
        "nuevo": "SIN DEBATE — nadie la ha atacado todavía, no cuenta aún",
        "en_curso": "DESAFIADA — su autor debe defender, refinar o conceder",
        "defendida": "DEFENDIDA — sobrevivió a un desafío",
        "refinada": "REFINADA — reescrita tras un desafío, puede volver a atacarse",
        "activo": "ACTIVA",
        "falsado": "FALSADA — cayó con contra-evidencia",
        "falsador_invocado": "FALSADOR INVOCADO — su autor debe rebatirlo o CAE",
        "sin_responder": "SIN RESPONDER — atacada y nadie contestó",
    }
    # ── Modo indice (por defecto): una linea por hipotesis ───────────────────
    if not str(id or "").strip():
        out = list(cabecera)
        mios = [h for h in hs if not h.get("heredada")]
        heredadas = [h for h in hs if h.get("heredada")]
        if mios:
            out.append(f"DE ESTE PERIODO ({len(mios)}):")
            for h in mios:
                marca = ""
                if h.get("duplicate_of"):
                    marca = f"  [DUPLICADO de {h['duplicate_of']}]"
                elif h.get("challenged_by"):
                    marca = f"  [atacada por {h['challenged_by']}]"
                out.append(f"  {h['id']} [{h['status']}] por {h.get('proposed_by','?')}"
                           f"{marca}\n     {_resumen_de(h)}")
        pend = [h for h in hs if h.get("pendiente")]
        heredadas = [h for h in heredadas if not h.get("pendiente")]
        if pend:
            out.append(f"\nSIN DEBATIR AÚN, de periodos anteriores ({len(pend)}) — "
                       "empieza por estas:")
            for h in pend:
                out.append(f"  {h['id']} [{h['status']}] {_resumen_de(h)}")
        if heredadas:
            alim = sum(1 for h in heredadas if h.get("tocada"))
            out.append(f"\nDE PERIODOS ANTERIORES ({len(heredadas)}, "
                       f"{alim} tocada(s) en este):")
            for h in heredadas:
                probada = ", ".join(h.get("periods_tested") or []) or "?"
                marca = "  ← ya la tocaste aquí" if h.get("tocada") else ""
                out.append(f"  {h['id']} [{h['status']}] [{probada}]{marca}"
                           f"\n     {_resumen_de(h)}")
        out.append("\nPara ver una entera con su falsador y su evidencia: "
                   "list_hypotheses(id=\"H-NNN\").")
        return "\n".join(out)

    # ── Modo detalle: UNA entera ─────────────────────────────────────────────
    pedida = str(id).strip().upper()
    hs = [h for h in BUZON["hypotheses"] if h["id"].upper() == pedida]
    if not hs:
        existentes = ", ".join(x["id"] for x in BUZON["hypotheses"][:20])
        return f"No existe {pedida}. Las que hay: {existentes}"

    out = list(cabecera)
    for h in hs:
        prev = h.get("periods_tested") or []
        marca_p = f" | periodos: {', '.join(prev)}" if len(prev) > 1 else ""
        out.append(f"\n{h['id']} [{etiquetas.get(h['status'], h['status'])}] "
                   f"por {h.get('proposed_by', '?')}{marca_p}")
        out.append(f"  QUÉ        : {h.get('pattern', h.get('thesis', ''))}")
        if h.get("mechanism"):
            out.append(f"  POR QUÉ    : {h['mechanism']}")
        if h.get("prediction"):
            out.append(f"  PREDICE    : {h['prediction']}")
        if h.get("falsifier"):
            out.append(f"  SE FALSA SI: {h['falsifier']}")
        ev = h.get("evidence") or []
        out.append(f"  Evidencia  : {len(ev)} notas — {', '.join(ev[:3])}"
                   f"{'...' if len(ev) > 3 else ''}")
        if h.get("duplicate_of"):
            out.append(f"  DUPLICADO de {h['duplicate_of']} — el parser la absorberá")
        if h.get("challenged_by"):
            out.append(f"  ATACADA por {h['challenged_by']}: {h.get('challenge_reason', '')}")
        if h.get("defense"):
            out.append(f"  DEFENSA    : {h['defense']}")
        n_ev = len(h.get("history") or [])
        if n_ev > 1:
            out.append(f"  ({n_ev} eventos en su historial)")
    for c in BUZON["collections"]:
        out.append(f"\nCOLECCIÓN '{c['name']}': {len(c['notes'])} elementos")
    return "\n".join(out)


@tool
def report_delta(endpoint: str, error: str) -> str:
    """Registrar el fallo de un endpoint o tool para revisión posterior."""
    with open(os.path.join(REPORTS_DIR, "e2_debate_deltalog.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint, "error": error,
        }, ensure_ascii=False) + "\n")
    return f"Logueado: {endpoint}"


REWRITER_PROMPT = """You are a search query reformulator for a privacy-preserving search proxy.

1. ANONYMIZE: replace personal information (names, dates, specific locations,
   biographical details) with generic terms, preserving conceptual content.
2. CONVERT TO KEYWORDS: 3-8 keywords separated by spaces. No sentences, no questions.

Return ONLY valid JSON: {"query": "keyword1 keyword2 keyword3"}"""


async def _rewrite_query(query: str) -> str:
    url = os.environ.get("TINFOIL_BASE_URL", "http://tinfoil-proxy:3302/v1")
    key = os.environ.get("TINFOIL_API_KEY", "NotRequired")
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                f"{url}/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": "gpt-oss-120b",
                      "messages": [{"role": "system", "content": REWRITER_PROMPT},
                                   {"role": "user", "content": query}],
                      "temperature": 0.0, "reasoning_effort": "low"},
            )
            r.raise_for_status()
            return json.loads(r.json()["choices"][0]["message"]["content"])["query"]
    except Exception:
        return query


@tool
async def searxng_search(query: str) -> str:
    """Buscar externamente. La query se anonimiza antes de salir."""
    rewritten = await _rewrite_query(query)
    blocked = os.environ.get("SEARXNG_BLOCKED_TERMS", "")
    if blocked:
        for term in (t.strip().lower() for t in blocked.split(",") if t.strip()):
            if term in rewritten.lower():
                return json.dumps({"error": f"Query bloqueada: contiene '{term}'"})
    base = os.environ.get("SEARXNG_URL", "http://searxng:8080")
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{base}/search", params={"q": rewritten, "format": "json"})
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return json.dumps({"error": f"SearXNG falló: {e}"})
    results = [{"title": x.get("title", ""), "url": x.get("url", ""),
                "snippet": x.get("content", ""), "engine": x.get("engine", "")}
               for x in data.get("results", [])][:12]
    return json.dumps({"query": rewritten, "results": results}, ensure_ascii=False)


async def get_mcp_token() -> str:
    rest = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000")
    cid = os.environ.get("MCP_CLIENT_ID", "")
    csec = os.environ.get("MCP_CLIENT_SECRET", "")
    if not cid or not csec:
        raise RuntimeError("MCP_CLIENT_ID y MCP_CLIENT_SECRET son requeridos")
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.post(f"{rest}/oauth/token",
                         data={"grant_type": "client_credentials",
                               "client_id": cid, "client_secret": csec},
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        r.raise_for_status()
        return r.json()["access_token"]


COMPARTIDO = """Debates con otro agente sobre el resultado de un análisis de teoría fundamentada
aplicado a un corpus de notas personales. El análisis ya produjo un libro de códigos y una lista
de relaciones entre códigos, con los note_id que respaldan cada una. Ese material es tu punto de
partida y está en el mensaje de apertura.

TU TAREA: defender una categoría central distinta a la del otro agente, y salir del debate con
hipótesis que sobrevivan al escrutinio del otro. Varias hipótesis pueden coexistir. Una capa
emocional y una capa práctica no compiten: son hipótesis distintas sobre el mismo material.

PROHIBIDO, sin excepciones:
- Inventar narradores, personajes, nombres propios o géneros. El corpus no tiene protagonistas.
- Escribir crónicas, relatos de un día, o storylines. No estás contando una historia.
- Afirmar nada sobre el contenido del corpus sin al menos un note_id que lo respalde.
- Citar note_ids que no aparezcan en el material de apertura o que no devuelva una tool.

Si no tienes evidencia para algo, lo descartas y lo dices. Una hipótesis sin note_ids no existe.

REGLAS DURAS SOBRE note_ids Y PERIODO:
- Un note_id se escribe SIEMPRE completo, los 36 caracteres. NUNCA lo abrevies, ni en tu prosa
  ni al citarlo. "00000000-0000" no es un note_id: es basura y la tool lo rechaza.
- Solo cuentan notas DEL PERIODO analizado. El corpus contiene material de otros periodos,
  incluido material posterior al analizado. Ese material NO es evidencia sobre el periodo:
  citarlo sería validar una conclusión con notas escritas después.
  propose_hypothesis comprueba la fecha de cada note_id y rechaza los de fuera.
- Para pasar de una categoría a sus notas, usa notas_por_codigo. Es exacto y solo devuelve
  material del periodo. La búsqueda semántica en Fortemi es el último recurso, no el primero.
- Cuando aun así busques en Fortemi, filtra por fecha si la tool lo permite, y descarta lo que no puedas fechar dentro del periodo: lo que no se puede fechar no se cita.

REGLA SOBRE CÓDIGOS:
- Los nombres bajo MATERIAL DEL DEBATE son las categorías del libro de códigos, y cada una
  lleva cuántas notas tiene. NO son necesariamente los nombres que acepta el índice: el
  análisis previo consolidó y renombró. La lista buena la da listar_codigos(). Llámala una
  vez al principio y úsala como referencia. Nunca inventes nombres.

NUNCA digas que no has recibido el material ni pidas que te lo manden. El libro de códigos y
las relaciones están al final de estas mismas instrucciones, bajo "MATERIAL DEL DEBATE". Siempre.
Si te sientes perdido, es que dejaste de mirarlo: vuelve a leerlo y sigue.

IDIOMA: respondes SIEMPRE en español, pase lo que pase. Aunque el otro agente cambie de idioma,
aunque una tool devuelva inglés, aunque pierdas el hilo. Siempre español.

TUS HERRAMIENTAS:
- Las tools de Fortemi son de SOLO LECTURA: lees y buscas, no escribes.
  Y NO son opcionales. La muestra que tienes en el material es la PUERTA, no la casa: es
  un recorte del corpus, no el corpus. Antes de que una hipótesis tuya llegue a DEFENDIDA
  tienes que haberla verificado contra Fortemi, y eso son dos cosas concretas:
    1. get_note sobre los note_ids que citas — leer el texto entero, no solo la frase
       del índice. La frase es un extracto; la nota puede decir algo distinto.
    2. Al menos una búsqueda que intente CONTRADECIRTE con material que no está en la
       muestra. Si tu mecanismo es cierto, debería aguantar fuera de tu recorte. Si no
       encuentras nada que lo contradiga, dilo: eso la refuerza más que cualquier defensa.
  Una hipótesis construida solo con el índice está construida sobre una muestra que la
  vio nacer. Eso no es prueba, es eco.
- get_note devuelve created_at_utc. Úsalo: saber de cuándo es cada nota te deja ver si
  algo evoluciona dentro del propio periodo o aparece de golpe.
- search_notes y get_note son las útiles. get_note necesita el id completo.
- La tool 'knowledge' es tu almacén personal y está VACÍO. No la explores, no hay nada ahí.
- Tienes 20 CONSULTAS por turno (listar_codigos, notas_por_codigo, get_note, búsquedas).
  Las tools de REGISTRO no se agotan nunca: propose_hypothesis, challenge_hypothesis,
  defend_hypothesis, refine_hypothesis, concede_hypothesis, reinforce_hypothesis y
  emit_collection funcionan siempre, aunque hayas gastado las consultas.
- Si vuelves a pedir una consulta que ya hiciste en este turno, se te devuelve el mismo
  resultado sin coste. No pasa nada por pedirla: el historial se recorta cuando lees
  muchas notas de golpe, y es normal que dejes de verla.
- Pero hay algo que sí depende de ti: LO QUE ESCRIBES EN TU MENSAJE NO SE RECORTA IGUAL
  que un resultado de tool. Si encuentras un note_id que importa, escríbelo en tu mensaje
  aunque sea de pasada. Lo que registras con las tools está a salvo; lo que solo leíste,
  no. No acumules hallazgos en la cabeza para el final: regístralos según los tengas.
- Llama las consultas en tandas paralelas, no de una en una.
- searxng_search busca afuera. Úsala cuando el material toque una disciplina real y quieras
  ver si lo que dice el autor coincide o diverge con lo que esa disciplina sostiene.
- propose_hypothesis registra una hipótesis. Queda en estado NUEVO.
- challenge_hypothesis ataca una hipótesis existente con contra-evidencia. Pasa a EN_CURSO.
- concede_hypothesis aceptas que tu hipótesis fue falsada. Solo el proponente puede conceder.
- list_hypotheses te muestra todas las hipótesis y su estado. Es tu memoria fiable.
- listar_codigos() PRIMERO, una sola vez, al empezar tu primer turno. Te da las CATEGORÍAS
  del libro con sus notas. Detrás hay cientos de códigos crudos del análisis, casi todos con
  una sola nota: no los pidas salvo que las categorías se te queden cortas. Te devuelve todos los
  nombres consultables con cuántas notas tiene cada uno. Los nombres del libro de códigos NO
  siempre coinciden con los del índice: el análisis previo consolidó y renombró categorías.
  La lista de listar_codigos es la verdad. Trabaja solo con esos nombres.
- notas_por_codigo(code) te da las notas de un código, con una frase de cada una. Acepta
  nombres aproximados, pero si te dice que no encuentra nada, NO pruebes variantes: llama a
  listar_codigos y usa un nombre real. Adivinar quema tus 20 tool calls sin producir nada.
- Una categoría con n_notas 0 en el material no tiene evidencia recuperable. Ignórala y
  trabaja con las que sí la tienen: están en listar_codigos.
- TIENES DOS MEMORIAS Y HAY QUE MIRAR LAS DOS:
    list_hypotheses()          → lo de ESTE debate, tuyo y del otro agente.
    buscar_hipotesis_fortemi() → lo acumulado de periodos ANTERIORES. El índice son las
                                 notas con tag 'thesis' en Fortemi; el parser las escribe
                                 al final de cada debate.
  Ninguna sustituye a la otra. Antes de proponer, mira las dos. Después decides:
    · cubierta por una previa   → reinforce_hypothesis con notas de aquí, o desafíala
    · parecida pero no igual    → propón la tuya y explica en qué se diferencia
    · no aparece en ninguna     → es nueva: PROPONLA. No dejes de registrar por prudencia.
  El objetivo es que no se dupliquen, no que dejes de proponer.
- emit_collection agrupa un tema: sus hipótesis Y las notas del corpus que le pertenecen.
- report_delta registra el fallo de una tool.

CÓMO DEBATIR — SISTEMA DE ESTADOS:

El debate funciona por ciclos de propuesta → desafío → defensa/concesión:

1. PROPONER: cuando tienes una afirmación con note_ids que la respalden y está lista para ser
   atacada, la registras con propose_hypothesis. Queda en estado NUEVO.

2. DESAFIAR: cuando el otro propone algo que crees que no se sostiene, lo atacas con
   challenge_hypothesis, trayendo note_ids del periodo que lo contradigan. Pasa a EN_CURSO.

3. DEFENDER o CONCEDER: si tu hipótesis fue desafiada, respondes. Si la contra-evidencia no
   la tumba, argumentas por qué y sigues. Si la contra-evidencia la tumba, llamas
   concede_hypothesis y la dejas en FALSADA.

4. SOBREVIVIR: una hipótesis que fue desafiada y no fue concedida queda EN_CURSO. Al cerrar
   el debate, todas las EN_CURSO pasan a ACTIVO y son candidatas para Fortemi.
   Las que están en NUEVO y nunca fueron desafiadas NO pasan a ACTIVO: quedan sin debate.

REGLA OBLIGATORIA — cada turno debes hacer al menos UNA de estas tres cosas:
- Registrar una hipótesis nueva (propose_hypothesis)
- Desafiar una existente (challenge_hypothesis)
- Conceder una tuya que fue desafiada (concede_hypothesis)

Si no haces ninguna de las tres, estás desperdiciando el turno. El debate no avanza con
opiniones sueltas: avanza con proposiciones registradas que se atacan y se defienden o caen.

No repitas argumentos que ya hiciste. Máximo 250 palabras por mensaje. Sin tablas ni headers.
Cuando cites un note_id en tu mensaje, escríbelo completo aunque quede feo.

Cuando ya no queden afirmaciones vivas que atacar ni material nuevo que proponer, responde con un
mensaje VACÍO. Eso cierra el debate limpiamente. No escribas 'he terminado' ni te despidas:
mensaje vacío. No alargues el debate por alargarlo — si lo que aguantaba ya está registrado,
cierra."""

CONTEXTO_CORPUS = """

═══════════════════ CONTEXTO DE ESTE CORPUS ═══════════════════
Este bloque es lo único que cambia si el sistema se corre sobre el material de otra
persona. Todo lo anterior vale igual para cualquier corpus.

QUÉ ES: notas personales de auto-observación acumuladas durante años — transcripciones
de notas de voz, conversaciones con IA exportadas, escritos. El autor piensa en voz alta:
se contradice, evoluciona, vuelve sobre lo mismo meses después. Que algo se repita no es
redundancia, es dato.

EL AUTOR FORMULA SUS PROPIAS HIPÓTESIS, Y ESAS TIENEN PRIORIDAD.
En estas notas puede haber afirmaciones que el autor hizo conscientemente sobre sí mismo:
"yo funciono así", "esto me pasa porque", "creo que mi problema es". No son observaciones
tuyas sobre él: son tesis suyas.

Búscalas activamente. Cuando encuentres una, formalízala con propose_hypothesis igual
que cualquier otra — pattern, mechanism, prediction, falsifier, note_ids — y déjala
pasar por el mismo ciclo de desafío. Que la dijera él no la hace verdadera; la hace
importante. Una autoteoría que no aguanta el debate es un hallazgo tan valioso como una
que sí, y probablemente más útil.

Estas son distintas de las que tú deduces cruzando códigos. Marca en el pattern que la
afirmación es suya: "El autor sostiene que...". Y si el corpus la contradice, esa es
exactamente la clase de cosa que hay que registrar.


TRANSCRIPCIÓN: pueden haber notas que vienen de audio, así que hay palabras mal transcritas, nombres
propios deformados y términos técnicos rotos. Si algo no encaja, considera que puede ser
un error de transcripción antes de construir una teoría sobre ello. Si te parece
relevante, regístralo con report_delta.

IDIOMAS: el corpus puede mezclar idiomas, a veces dentro de la misma nota.

HARDWARE: el sistema corre en una máquina modesta. No pidas volcados masivos: consultas
acotadas y específicas.
═══════════════════════════════════════════════════════════════
"""

AGENTE_A = """

Eres el agente A.

Tu entrada al material son las categorías con más masa de evidencia: las que aparecen en más
notas y en más relaciones. Defiendes que la categoría central del periodo es la que el corpus
sostiene con más peso.

Tu sesgo a vigilar: confundir frecuencia con importancia. Algo puede repetirse por ser trivial.
Si el otro agente te lo señala, tienes que responder con evidencia, no con insistencia."""

AGENTE_B = """

Eres el agente B.

Tu entrada al material son las tensiones: relaciones que se contradicen entre sí, categorías que
aparecen poco pero en momentos densos, cosas que no encajan con el resto. Defiendes que la
categoría central del periodo es la que explica lo que no cuadra.

Tu sesgo a vigilar: confundir rareza con profundidad. Algo puede aparecer poco por ser marginal.
Si el otro agente te lo señala, tienes que responder con evidencia, no con insistencia."""


def construir_apertura(datos: dict, max_chars: int) -> str:
    cats = datos.get("categorias", [])
    triples = datos.get("triples_axiales", [])
    partes = [
        "==================== MATERIAL DEL DEBATE ====================",
        f"Periodo analizado: {datos.get('period')}. "
        f"{datos.get('n_notas')} notas, {datos.get('n_segmentos')} segmentos.",
        "",
        "Este material está SIEMPRE disponible en tus instrucciones. Si en algún "
        "momento crees que no lo recibiste, vuelve a leer aquí: está abajo. "
        "Nunca pidas que te lo manden.",
        "",
        "CATEGORÍAS DEL LIBRO DE CÓDIGOS. El número entre corchetes es cuántas notas del "
        "periodo tienen evidencia recuperable. Las de [0 notas] no sirven: ignóralas. "
        "Para consultar, usa los nombres que devuelve listar_codigos(), no estos.",
    ]
    for c in cats:
        n = c.get("n_notas")
        marca = f" [{n} notas]" if isinstance(n, int) else ""
        partes.append(f"- {c.get('code')}{marca}: {c.get('definition')}")
    partes += ["", "RELACIONES ENTRE CÓDIGOS (con note_ids de respaldo):"]
    for t in triples:
        partes.append(
            f"- SI {t.get('condition')} | ENTONCES {t.get('action')} "
            f"| RESULTA {t.get('result')} "
            f"| notas ({t['n_evidence']}): {', '.join(t['note_ids'][:8])}"
        )
    previas = datos.get("hipotesis_previas") or []
    if previas:
        # UNA LINEA POR HIPOTESIS, no el texto entero.
        #
        # Antes iban aqui el pattern, el mechanism y la prediction completos de
        # cada una -- y list_hypotheses los repetia otra vez. Con muchas heredadas
        # eso puede ser la mayor parte del prompt de sistema, y se pagaba dos
        # veces. Con decenas no cabe, y la herencia acabaria empujando fuera el
        # material del periodo, que es lo unico que este trimestre puede aportar.
        #
        # Aqui va el indice. El texto completo de una concreta se pide con
        # list_hypotheses(id="H-003"), y solo de la que interese.
        pend = [h for h in previas if h.get("_pendiente")]
        resto = [h for h in previas if not h.get("_pendiente")]
        if pend:
            # Estas van ENTERAS: nunca se debatieron, asi que no hay "lo que
            # sobrevivio" que resumir. Son deuda pendiente, y lo primero que
            # este periodo puede hacer es atacarlas.
            partes += ["", f"SIN DEBATIR AÚN, de periodos anteriores ({len(pend)}). "
                           "Nadie las atacó todavía: empieza por aquí."]
            for h in pend:
                partes.append(f"- {h['id']} (propuesta en {h.get('_periodo_origen') or h.get('origin_period','?')})")
                partes.append(f"    QUÉ: {h.get('pattern','')}")
                if h.get("mechanism"):
                    partes.append(f"    POR QUÉ: {h['mechanism']}")
                if h.get("prediction"):
                    partes.append(f"    PREDICE: {h['prediction']}")
                if h.get("falsifier"):
                    partes.append(f"    UN CASO QUE LA TUMBARÍA: {h['falsifier']}")
        if resto:
            partes += ["", "HIPÓTESIS DE PERIODOS ANTERIORES (siguen vivas, no las dupliques):"]
            for h in resto:
                probada = ", ".join(h.get("periods_tested") or []) or h.get("origin_period", "?")
                partes.append(f"- {h['id']} [{probada}] {_resumen_de(h)}")
        partes.append("  Para ver una entera: list_hypotheses(id=\"H-NNN\"). Si el material "
                      "de este periodo cumple su predicción, refuérzala con "
                      "reinforce_hypothesis; si la contradice, desafíala. No hace falta "
                      "que las mires todas: mira las que tu material toque.")

    partes += ["",
               f"TODO note_id que cites debe ser una nota del periodo {datos.get('period')}. "
               "Los note_id de arriba ya están verificados; cualquier otro se comprueba "
               "por fecha al registrarlo.",
               "================== FIN DEL MATERIAL =================="]
    texto = "\n".join(partes)
    return texto[:max_chars]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="debate_input.json de prep_debate_input.py")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--max-rounds", type=int, default=15,
                    help="Techo de seguridad. El cierre normal es el mensaje vacío.")
    ap.add_argument("--stall-seconds", type=int, default=300,
                    help="Segundos sin mensajes nuevos antes de dar el canal por parado. "
                         "Un turno vacio no envia nada y la conversacion se queda colgada.")
    ap.add_argument("--max-improductivos", type=int, default=4,
                    help="Turnos seguidos sin ejecutar tools antes de dar un agente por "
                         "degenerado y cerrar. Evita quemar presupuesto en bucles vacios.")
    ap.add_argument("--max-minutes", type=int, default=45,
                    help="Tope de reloj. Evita tener que interrumpir a mano.")
    ap.add_argument("--max-usd", type=float, default=0.50)
    ap.add_argument("--max-calls", type=int, default=400)
    ap.add_argument("--modelo-a", default="gemma4-31b")
    ap.add_argument("--modelo-b", default="deepseek-v4-flash")
    ap.add_argument("--apertura-max-chars", type=int, default=24000)
    ap.add_argument("--clean", action="store_true")
    args = ap.parse_args()

    PRESUPUESTO["tope_usd"] = args.max_usd
    PRESUPUESTO["tope_llamadas"] = args.max_calls

    if args.clean:
        import glob
        for f in glob.glob(os.path.join(DATA_DIR, "e2_debate_*.db")) + \
                 glob.glob(os.path.join(REPORTS_DIR, "e2_debate_*.jsonl")):
            os.remove(f)
            print(f"[E2] Limpiado: {f}")

    with open(args.input, "r", encoding="utf-8") as f:
        datos = json.load(f)

    global NOTE_IDS_SEMILLA
    NOTE_IDS_SEMILLA = set(datos.get("note_ids_semilla") or datos.get("note_ids_validos") or [])
    PERIODO["label"] = str(datos.get("period") or "?")
    PERIODO["start"] = datos.get("period_start")
    PERIODO["end"] = datos.get("period_end")

    print(f"[E2] Periodo {PERIODO['label']} "
          f"({PERIODO['start'] or 'sin límites'} .. {PERIODO['end'] or ''})")
    print(f"[E2] {len(NOTE_IDS_SEMILLA)} note_ids semilla "
          f"| {len(datos.get('categorias', []))} categorías "
          f"| {len(datos.get('triples_axiales', []))} relaciones")
    if not PERIODO["start"]:
        print("[E2] AVISO: sin límites de periodo. No se filtrarán note_ids por fecha.")

    print("[E2] Obteniendo OAuth token...")
    token = await get_mcp_token()
    REST_TOKEN["value"] = token
    print(f"[E2] Token OK ({token[:12]}...)")

    base_url = os.environ.get("TINFOIL_BASE_URL", "http://tinfoil-proxy:3302/v1")
    api_key = os.environ.get("TINFOIL_API_KEY", "NotRequired")
    cfg_a = OpenAIConfig(model=args.modelo_a, base_url=base_url, api_key=api_key,
                         reasoning_effort="high")
    cfg_b = OpenAIConfig(model=args.modelo_b, base_url=base_url, api_key=api_key,
                         reasoning_effort="high")

    read_mcp = MCPToolkit(MCPServerConfig(
        server_url=os.environ.get("FORTEMI_MCP_URL", "http://fortemi-mcp:3002"),
        authorization_token=token,
        allowed_tools=READ_ONLY_TOOLS,
    ))

    tools_comunes = [_robusta(t) for t in
                     [read_mcp, searxng_search, notas_por_codigo, listar_codigos,
                      buscar_hipotesis_fortemi, propose_hypothesis,
                      challenge_hypothesis, defend_hypothesis, refine_hypothesis,
                      concede_hypothesis, reinforce_hypothesis,
                      emit_collection, list_hypotheses, report_delta]]

    def construir(nombre, prompt_especifico, cfg, modelo, db):
        return Agent(
            nombre,
            # Tres capas: el metodo (agnostico), este corpus (sustituible) y el papel
            # del agente. Solo la de en medio cambia si se corre para otra persona.
            prompt=COMPARTIDO + CONTEXTO_CORPUS + prompt_especifico + "\n\n" + MATERIAL,
            config=cfg,
            tools=tools_comunes,
            knowledge=KnowledgeConfig(
                store=SqliteKnowledgeStore(os.path.join(DATA_DIR, db)),
                write_event_log=True,
            ),
            assembly=[WorkingMemoryPolicy(), SlidingWindowPolicy(max_events=60)],
            middleware=[
                LoggingMiddleware(),
                Middleware(E2Middleware, agent_name=nombre, modelo=modelo, max_tool_calls=20),
                TokenLimiter(max_tokens=64000),
            ],
        )

    os.makedirs(args.out_dir, exist_ok=True)
    BUZON_JSONL["path"] = os.path.join(args.out_dir, "e2_debate_buzon.jsonl")
    TOOLS_LOG_PATH["path"] = os.path.join(args.out_dir, "e2_debate_tools.jsonl")
    if args.clean and os.path.exists(TOOLS_LOG_PATH["path"]):
        os.remove(TOOLS_LOG_PATH["path"])
    if args.clean and os.path.exists(BUZON_JSONL["path"]):
        os.remove(BUZON_JSONL["path"])
        print(f"[E2] Limpiado: {BUZON_JSONL['path']}")
    elif os.path.exists(BUZON_JSONL["path"]):
        rec = 0
        with open(BUZON_JSONL["path"], encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    d = json.loads(linea)
                except Exception:
                    continue
                tipo = d.pop("tipo", ""); d.pop("ts", None)
                if tipo == "hypothesis":
                    BUZON["hypotheses"].append(d); rec += 1
                elif tipo == "collection":
                    BUZON["collections"].append(d); rec += 1
                elif tipo == "challenge":
                    h = next((h for h in BUZON["hypotheses"] if h["id"] == d.get("hypothesis_id")), None)
                    if h:
                        h["status"] = "en_curso"
                        h["challenged_by"] = d.get("challenger")
                        h["counter_evidence"] = d.get("counter_evidence", [])
                        h["challenge_reason"] = d.get("reason", "")
                    rec += 1
                elif tipo == "concede":
                    h = next((h for h in BUZON["hypotheses"] if h["id"] == d.get("hypothesis_id")), None)
                    if h:
                        h["status"] = "falsado"
                    rec += 1
                # SE PERDIAN AL REANUDAR. El replay solo re-aplicaba challenge y
                # concede: un defense o un refine anteriores se persistian (con
                # los tipos "defense", "refine", "reinforce") pero nadie los
                # volvia a aplicar. Al retomar una corrida interrumpida, una
                # hipotesis defendida volvia como si nunca se hubiera defendido
                # -- y peor, seguia en en_curso, asi que caia a sin_responder al
                # cerrar. El trabajo del debate se tiraba en silencio.
                elif tipo == "defense":
                    h = next((h for h in BUZON["hypotheses"] if h["id"] == d.get("hypothesis_id")), None)
                    if h:
                        h["status"] = "defendida"
                        h["defense"] = d.get("defense", "")
                        if d.get("rebate_falsifier"):
                            h["rebate_falsifier"] = d["rebate_falsifier"]
                        for nid in (d.get("extra_evidence") or []):
                            if nid not in h.setdefault("evidence", []):
                                h["evidence"].append(nid)
                    rec += 1
                elif tipo == "refine":
                    h = next((h for h in BUZON["hypotheses"] if h["id"] == d.get("hypothesis_id")), None)
                    if h:
                        h["status"] = "refinada"
                        for campo in ("pattern", "mechanism", "prediction", "falsifier"):
                            if d.get(campo):
                                h[campo] = d[campo]
                    rec += 1
                elif tipo == "reinforce":
                    h = next((h for h in BUZON["hypotheses"] if h["id"] == d.get("hypothesis_id")), None)
                    if h:
                        for nid in (d.get("evidence") or d.get("extra_evidence") or []):
                            if nid not in h.setdefault("evidence", []):
                                h["evidence"].append(nid)
                        per = d.get("period")
                        if per and per not in h.setdefault("periods_tested", []):
                            h["periods_tested"].append(per)
                        h["tocada"] = True
                    rec += 1
        if rec:
            print(f"[E2] Recuperados {rec} registros de una corrida anterior. "
                  "Usa --clean para empezar de cero.")

    for h in datos.get("hipotesis_previas") or []:
        if any(x["id"] == h.get("id") for x in BUZON["hypotheses"]):
            continue
        BUZON["hypotheses"].append({
            "id": h.get("id"), "asunto": h.get("asunto", h.get("collection", h.get("category", ""))),
            "resumen": h.get("resumen", ""),
            # Se propuso en un periodo anterior y NADIE la debatio. No es una
            # heredada normal: no ha sobrevivido nada todavia. Llega entera a
            # proposito, para que este periodo pueda empezar por ella.
            "pendiente": bool(h.get("_pendiente")),
            # La clave con la que vive en pendientes.json. Viaja intacta para
            # que el parser sepa cual sacar del archivo: el id NO sirve,
            # porque el prep las renumera al cargarlas.
            "_clave": h.get("_clave", ""),
            "pattern": h.get("pattern", ""), "mechanism": h.get("mechanism", ""),
            "prediction": h.get("prediction", ""), "falsifier": h.get("falsifier", ""),
            # La evidencia de su periodo de origen NO se vacia. Antes se ponia
            # evidence: [] y el agente la veia como "0 notas": para reforzarla
            # tenia que re-descubrir su evidencia desde cero, trabajo que una
            # propuesta nueva no exige. Por eso nadie alimentaba las heredadas.
            "evidence": list(h.get("evidence") or []),
            "evidence_by_period": dict(h.get("evidence_by_period") or {}),
            "origin_period": h.get("origin_period"),
            "periods_tested": list(h.get("periods_tested") or []),
            "proposed_by": "periodo_anterior", "status": h.get("status", "activo"),
            "challenged_by": None, "counter_evidence": [], "challenge_reason": "",
            "defense": "", "history": [], "heredada": True,
            # Marca de si ESTE periodo la toco. El parser solo escribe las
            # tocadas: una heredada que nadie examino es contexto de entrada, no
            # resultado de esta corrida. Sin esto, cada periodo re-materializa
            # las mismas hipotesis y engorda su nota con un bloque identico.
            "tocada": False,
        })
    if datos.get("hipotesis_previas"):
        print(f"[E2] {len(datos['hipotesis_previas'])} hipótesis heredadas de periodos anteriores")

    n_cod, n_notas_idx, pct_lit = cargar_indice_codigos(datos, args.input)
    if n_cod:
        print(f"[E2] Índice de códigos: {n_cod:,} códigos, {n_notas_idx:,} notas alcanzables "
              f"| {pct_lit:.0f}% de citas literales")
    else:
        print("[E2] AVISO: sin code_index.json. notas_por_codigo no funcionará. "
              "Corre prep_debate_input.py sobre el mismo run-dir.")

    global MATERIAL
    MATERIAL = construir_apertura(datos, args.apertura_max_chars)
    print(f"[E2] Material en prompt de sistema: {len(MATERIAL):,} caracteres")

    agente_a = construir("agente_a", AGENTE_A, cfg_a, args.modelo_a, "e2_debate_agente_a.db")
    agente_b = construir("agente_b", AGENTE_B, cfg_b, args.modelo_b, "e2_debate_agente_b.db")

    print("[E2] Abriendo hub...")
    hub = await Hub.open(MemoryKnowledgeStore(), ttl_sweep_interval=0)
    a_c = await hub.register(agente_a, attach_plugin=False)
    b_c = await hub.register(agente_b, attach_plugin=False)
    id_a_nombre = {a_c.agent_id: "agente_a", b_c.agent_id: "agente_b"}

    print("[E2] Abriendo canal conversation (2 participantes, sin orden de turnos)...")
    channel = await a_c.open(type="conversation", target=[b_c.agent_id])
    print(f"[E2] Canal: {channel.channel_id}")

    kickoff = (
        f"Empieza el debate sobre el periodo {PERIODO['label']}. "
        "El libro de códigos y las relaciones están en tus instrucciones, siempre "
        "disponibles: no dependen de este canal y no se pierden. "
        "Agente A abre exponiendo su categoría. Agente B responde con la suya."
    )
    await channel.send(kickoff)

    ids_agentes = {a_c.agent_id, b_c.agent_id}
    tope_mensajes = args.max_rounds * 2
    print("\n" + "=" * 62 + "\nDEBATE EN VIVO\n" + "=" * 62)

    vistos, envelopes = set(), []
    ultimo_reporte = [0]
    ultimo_mensaje = [0.0]
    ultimo_latido = [0.0]
    cerrado = False
    loop = asyncio.get_running_loop()
    ultimo_mensaje[0] = ultimo_latido[0] = loop.time()
    limite_reloj = loop.time() + args.max_minutes * 60.0
    deadline = limite_reloj + 300.0
    motivo = "desconocido"

    try:
        while not cerrado and loop.time() < deadline:
            try:
                wal = await hub.read_wal(channel.channel_id)
            except Exception as e:
                print(f"[E2] Error leyendo WAL: {e}")
                await asyncio.sleep(2.0)
                continue

            for env in wal:
                if env.envelope_id in vistos:
                    continue
                vistos.add(env.envelope_id)
                envelopes.append(env)
                quien = id_a_nombre.get(env.sender_id, str(env.sender_id)[:8])

                if env.event_type == EV_TEXT:
                    print(f"\n[{quien}] {env.event_data.get('text', '')}")
                elif env.event_type == EV_PACKET:
                    cuerpo = env.event_data.get("body", "")
                    if cuerpo:
                        print(f"\n[{quien}] {cuerpo}")
                elif env.event_type == EV_CHANNEL_CLOSED:
                    print(f"\n[E2] Canal cerrado: {env.event_data.get('reason', 'unknown')!r}")
                    cerrado = True
                    motivo = "canal_cerrado"
                    break

            if cerrado:
                break

            n_msgs = sum(1 for e in envelopes
                         if e.sender_id in ids_agentes
                         and e.event_type in (EV_TEXT, EV_PACKET))
            if n_msgs != ultimo_reporte[0]:
                ultimo_reporte[0] = n_msgs
                ultimo_mensaje[0] = loop.time()
                # ¿El último mensaje vino de un turno que ejecutó tools?
                ultimo = next((e for e in reversed(envelopes)
                               if e.sender_id in ids_agentes), None)
                quien = id_a_nombre.get(getattr(ultimo, "sender_id", ""), "")
                if quien in IMPRODUCTIVOS:
                    if TOOLS_ESTE_TURNO.get(quien, 0) == 0:
                        IMPRODUCTIVOS[quien] += 1
                        if IMPRODUCTIVOS[quien] >= 2:
                            print(f"[E2] {quien} lleva {IMPRODUCTIVOS[quien]} turnos sin "
                                  "ejecutar ninguna tool.")
                    else:
                        IMPRODUCTIVOS[quien] = 0
                    TOOLS_ESTE_TURNO[quien] = 0
                # DOS EJES, no uno. Un periodo que solo alimenta hipotesis
                # anteriores y no crea ninguna es un BUEN periodo: la herencia
                # esta haciendo su trabajo. Un contador que solo suma "hipotesis"
                # empuja a proponer para que el numero crezca, que es lo contrario
                # de lo que se quiere. Y arrancar en "N vivas" antes de que este
                # periodo haya producido nada confunde inventario con resultado.
                _nuevas = [h for h in BUZON["hypotheses"] if not h.get("heredada")]
                _her = [h for h in BUZON["hypotheses"] if h.get("heredada")]
                _alim = [h for h in _her if h.get("tocada")]
                _fals = sum(1 for h in _nuevas + _alim if h["status"] == "falsado")
                partes_contador = [f"{len(_nuevas)} nuevas de {PERIODO['label']}"]
                if _fals:
                    partes_contador[0] += f" ({_fals} falsadas)"
                _pend = [h for h in _her if h.get("pendiente")]
                _her = [h for h in _her if not h.get("pendiente")]
                _alim = [h for h in _her if h.get("tocada")]
                if _pend:
                    _pend_tocadas = sum(1 for h in _pend if h.get("tocada"))
                    partes_contador.append(
                        f"{_pend_tocadas}/{len(_pend)} pendientes debatidas")
                if _her:
                    partes_contador.append(f"{len(_alim)}/{len(_her)} heredadas alimentadas")
                print(f"\n[E2] --- mensaje {n_msgs}/{tope_mensajes} | "
                      + " | ".join(partes_contador)
                      + f" | ${PRESUPUESTO['usd']:.3f} | {PRESUPUESTO['llamadas']} llamadas ---")

            # Latido: sin esto, un canal parado y un debate en marcha se ven igual.
            silencio = loop.time() - ultimo_mensaje[0]
            # Inactividad real: sin llamadas al modelo. Un turno largo NO esta parado.
            inactivo = (time.time() - ULTIMA_ACTIVIDAD["t"]) if ULTIMA_ACTIVIDAD["t"] else 0.0
            if loop.time() - ultimo_latido[0] >= 60.0:
                ultimo_latido[0] = loop.time()
                if silencio >= 60.0:
                    vacios = sum(TURNOS_VACIOS.values())
                    estado = ("trabajando" if inactivo < 90 else
                              f"sin actividad desde hace {inactivo / 60:.0f} min")
                    print(f"[E2] ...{estado}. {silencio / 60:.0f} min sin mensajes nuevos "
                          f"| {n_msgs} mensajes | {len(BUZON['hypotheses'])} hipótesis "
                          f"| ${PRESUPUESTO['usd']:.3f} | {PRESUPUESTO['llamadas']} llamadas"
                          + (f" | {vacios} turnos vacíos" if vacios else ""))

            # Canal parado: nadie habla y nadie va a hablar. Distinto de "va lento".
            # Solo esta parado si ADEMAS no hay actividad. Un turno de 20 tool calls
            # puede pasar de 4 minutos sin emitir mensaje y esta perfectamente vivo.
            if silencio >= args.stall_seconds and inactivo >= args.stall_seconds:
                vacios = sum(TURNOS_VACIOS.values())
                motivo = (f"canal parado: {inactivo / 60:.0f} min sin actividad "
                          f"y {silencio / 60:.0f} min sin mensajes"
                          + (f" tras {vacios} turno(s) cerrado(s) sin contenido"
                             if vacios else ""))
                print(f"\n[E2] {motivo}.")
                if vacios:
                    print("[E2] Un turno sin texto ni tools no envía nada al canal, así que "
                          "el otro agente nunca se dispara. Puede ser cierre voluntario "
                          "(el prompt lo permite) o un fallo del modelo.")
                await channel.close()
                cerrado = True
                break

            degenerado = next((k for k, v in IMPRODUCTIVOS.items()
                               if v >= args.max_improductivos), None)
            if degenerado:
                motivo = (f"{degenerado} degenerado: {IMPRODUCTIVOS[degenerado]} turnos "
                          "seguidos sin ejecutar ninguna tool")
            elif n_msgs >= tope_mensajes:
                motivo = f"tope de {args.max_rounds} rondas ({n_msgs} mensajes)"
            elif loop.time() > limite_reloj:
                motivo = f"tope de {args.max_minutes} minutos"
            elif PRESUPUESTO["usd"] >= PRESUPUESTO["tope_usd"]:
                motivo = f"presupuesto ${PRESUPUESTO['usd']:.3f}"
            elif PRESUPUESTO["llamadas"] >= PRESUPUESTO["tope_llamadas"]:
                motivo = f"{PRESUPUESTO['llamadas']} llamadas"
            else:
                await asyncio.sleep(2.0)
                continue

            print(f"\n[E2] Cerrando canal: {motivo}")
            await channel.close()
            cerrado = True

        if not cerrado:
            motivo = "timeout del bucle"
            print(f"\n[E2] {motivo}, cerrando.")
            try:
                await channel.close()
            except Exception:
                pass

    except (KeyboardInterrupt, asyncio.CancelledError):
        motivo = "interrupción"
        print("\n[E2] Interrupción. Guardando lo que hay...")
    finally:
        os.makedirs(args.out_dir, exist_ok=True)

        # Solo lo que paso por el debate se promociona. Lo que nadie ataco
        # ("nuevo") NO pasa a activo: una hipotesis sin examinar no esta probada.
        # desafiado NO se promueve — queda como está (sin debate).
        for h in BUZON["hypotheses"]:
            if h["status"] in ("defendida", "refinada"):
                h["status"] = "activo"
            elif h["status"] == "falsador_invocado":
                # El autor declaro que caeria si pasaba X, le trajeron X, y no lo rebatio.
                # Popper puro: la condicion la puso el, se cumplio, y no hubo defensa.
                h["status"] = "falsado"
                _evento(h, "falsado_por_defecto",
                        motivo="Falsador invocado y no rebatido antes de cerrar el debate.")
            elif h["status"] == "en_curso":
                # Atacada y sin respuesta: no cayo, pero tampoco se defendio.
                h["status"] = "sin_responder"

        theory = {"period": datos.get("period"), "hypotheses": []}
        for h in BUZON["hypotheses"]:
            theory["hypotheses"].append({**h, "period": datos.get("period")})
        with open(os.path.join(args.out_dir, "theory.json"), "w", encoding="utf-8") as f:
            json.dump(theory, f, ensure_ascii=False, indent=2)

        with open(os.path.join(args.out_dir, "collections.json"), "w", encoding="utf-8") as f:
            json.dump({"collections": BUZON["collections"]}, f, ensure_ascii=False, indent=2)

        transcript = os.path.join(args.out_dir, "e2_debate_transcript.jsonl")
        with open(transcript, "w", encoding="utf-8") as f:
            for env in envelopes:
                f.write(json.dumps({
                    "envelope_id": env.envelope_id, "event_type": str(env.event_type),
                    "sender": id_a_nombre.get(env.sender_id, env.sender_id),
                    "event_data": env.event_data, "created_at": str(env.created_at),
                }, ensure_ascii=False, default=str) + "\n")

        n_msgs_final = sum(1 for e in envelopes
                           if e.sender_id in id_a_nombre and e.event_type in (EV_TEXT, EV_PACKET))
        n_activas = sum(1 for h in BUZON["hypotheses"] if h["status"] == "activo")
        n_falsadas = sum(1 for h in BUZON["hypotheses"] if h["status"] == "falsado")
        n_sin_debate = sum(1 for h in BUZON["hypotheses"] if h["status"] == "nuevo")
        n_colgadas = sum(1 for h in BUZON["hypotheses"] if h["status"] == "sin_responder")
        n_refor = sum(1 for h in BUZON["hypotheses"] if len(h.get("periods_tested") or []) > 1)
        print("\n" + "=" * 62)
        print(f"motivo de cierre : {motivo}")
        print(f"mensajes         : {n_msgs_final}")
        print(f"hipótesis totales: {len(BUZON['hypotheses'])}")
        print(f"  activas (candidatas): {n_activas}")
        print(f"  falsadas (registro) : {n_falsadas}")
        print(f"  sin debate (nuevo) : {n_sin_debate}")
        n_dup = sum(1 for h in BUZON["hypotheses"] if h.get("duplicate_of"))
        n_falsador = sum(1 for h in BUZON["hypotheses"]
                         if any(e.get("event") == "falsado_por_defecto"
                                for e in h.get("history") or []))
        print(f"  atacadas sin respuesta: {n_colgadas}")
        if n_falsador:
            print(f"  caidas por su propio falsador: {n_falsador}")
        if n_dup:
            print(f"  marcadas duplicadas (las absorbe el parser): {n_dup}")
        if n_refor:
            print(f"  reforzadas entre periodos: {n_refor}")
        for h in BUZON["hypotheses"]:
            marca = {"activo": "✓", "falsado": "✗", "nuevo": "·",
                     "sin_responder": "~"}.get(h["status"], "?")
            if h.get("duplicate_of"):
                marca = "⊂"
            print(f"  {marca} {h['id']} [{h['status']}] [{h.get('collection', '')}] {len(h['evidence'])} notas")
        print(f"colecciones      : {len(BUZON['collections'])}")
        vacios_tot = sum(TURNOS_VACIOS.values())
        if vacios_tot:
            det = ", ".join(f"{k}={v}" for k, v in TURNOS_VACIOS.items() if v)
            print(f"\nTURNOS CERRADOS SIN CONTENIDO: {vacios_tot} ({det})")
            print("  Ni texto ni tools: el adapter no envía nada y el canal se queda "
                  "esperando. Puede ser cierre voluntario o fallo del modelo.")

        if TOOLS_LOG:
            from collections import Counter
            print("\nTOOLS LLAMADAS")
            por_estado = Counter(f["estado"] for f in TOOLS_LOG)
            print("  " + "  ".join(f"{k}={v}" for k, v in por_estado.most_common()))
            uso = Counter((f["agente"], f["tool"]) for f in TOOLS_LOG)
            por_tool = Counter(f["tool"] for f in TOOLS_LOG)
            print(f"  {'tool':<28}{'A':>5}{'B':>5}{'total':>7}  desperdiciadas")
            for tool, tot in por_tool.most_common():
                a = uso.get(("agente_a", tool), 0)
                b = uso.get(("agente_b", tool), 0)
                perd = sum(1 for f in TOOLS_LOG if f["tool"] == tool
                           and f["estado"] in ("repetida", "bloqueada"))
                marca = f"  {perd}" if perd else ""
                print(f"  {tool:<28}{a:>5}{b:>5}{tot:>7}{marca}")
            perdidas = sum(1 for f in TOOLS_LOG if f["estado"] in ("repetida", "bloqueada"))
            if perdidas:
                pct = perdidas / len(TOOLS_LOG) * 100
                print(f"  {perdidas} de {len(TOOLS_LOG)} llamadas no produjeron nada "
                      f"({pct:.0f}%): repetidas o bloqueadas.")
            print()
        print(f"llamadas LLM     : {PRESUPUESTO['llamadas']}")
        print(f"coste            : ${PRESUPUESTO['usd']:.4f}")
        if not BUZON["hypotheses"]:
            print("\nCERO HIPÓTESIS. Revisa el transcript.")
        print(f"\ntheory.json      -> {os.path.join(args.out_dir, 'theory.json')}")
        print(f"collections.json -> {os.path.join(args.out_dir, 'collections.json')}")
        print(f"transcript       -> {transcript}")
        if BUZON_JSONL["path"] and os.path.exists(BUZON_JSONL["path"]):
            print(f"buzón incremental-> {BUZON_JSONL['path']}")
        if TOOLS_LOG_PATH["path"] and os.path.exists(TOOLS_LOG_PATH["path"]):
            print(f"log de tools     -> {TOOLS_LOG_PATH['path']}")

        try:
            await hub.close()
        except Exception:
            pass


if __name__ == "__main__":
    # os._exit(0) evita el cuelgue de AG2 al cerrar, pero mata el proceso durante el
    # unwinding: cualquier excepcion de main() desaparecia sin traceback y con codigo 0.
    # Por eso se imprime primero y se sale con codigo distinto de cero si hubo fallo.
    _codigo = 0
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except BaseException:
        import traceback
        traceback.print_exc()
        _codigo = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(_codigo)