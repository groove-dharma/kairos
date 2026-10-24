#!/usr/bin/env python3
"""
daemon-delta — materializa deltas JSON en ElasticSearch y regenera los .md del Codex.

Sin dependencias externas: solo biblioteca estandar de Python.

MODELO
  Una DB del Codex = dos indices:
     codex-<slug>        indice VIVO      (solo la version vigente de cada cosa)
     codex-<slug>-hist   indice HISTORICO (todas las versiones, sufijo @vN)

  Cuatro niveles, un documento por nivel:
     tipo=db          _id = db
     tipo=collection  _id = collection:<collection_id>
     tipo=doc         _id = doc:<doc_id>
     tipo=bloque      _id = bloque:<bloque_id>

  Los _id son deterministas: reproducir un delta escribe exactamente lo mismo.

FLUJO
  inbox/*.json -> validar TODO -> aplicar TODO -> refresh -> concatenar -> .md
                       |                                                    |
                    falla algo                                            out/
                       v
                    failed/ + acuses/                        applied/ + acuses/

  ES PRIMERO, .md DESPUES, siempre. ES es el estado; el .md es una vista
  generada leyendo el indice entero. No se parchea el .md desde el delta:
  un renombrado o un reorden mueven cosas que el delta no menciona.

USO
  python3 daemon_delta.py init             instala la plantilla de indice
  python3 daemon_delta.py vigilar          bucle: se queda mirando inbox/
  python3 daemon_delta.py run              una pasada y sale
  python3 daemon_delta.py run --dry-run    valida sin escribir
  python3 daemon_delta.py md <indice>      regenera el .md sin aplicar deltas
  python3 daemon_delta.py reconstruir      rehace ES desde applied/
"""

import argparse
import json
import os
import re
import shutil
import time
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CODIGO = Path(__file__).resolve().parent

# En el contenedor la raiz es /deltas, montada desde
# la carpeta de deltas del host. Igual que el daemon de N1, el codigo no
# sabe nada de Windows: el montaje se resuelve en el compose.
HOGAR = Path(os.environ.get("DELTA_HOME", CODIGO))
INBOX = HOGAR / "inbox"
APPLIED = HOGAR / "applied"
FAILED = HOGAR / "failed"
OUT = HOGAR / "out"
ACUSES = HOGAR / "acuses"
REGISTRO_DIR = HOGAR / "estado"
REGISTRO = REGISTRO_DIR / "registro.json"
CARPETAS = (INBOX, APPLIED, FAILED, OUT, ACUSES, REGISTRO_DIR)

TEMPLATE = CODIGO / "codex_template.json"

# Ritmo copiado del daemon de N1 (POLL_INTERVAL_AUDIOS = 10, vuelta corta = 2)
ESPERA_LARGA = int(os.environ.get("DELTA_POLL", "10"))
ESPERA_CORTA = 2
REGISTRO_MAX = 1000

CONTENEDOR_ES = "fortemi-elasticsearch-1"
RED_ES = "fortemi_internal"

RE_SLUG = re.compile(r"^[a-z0-9_]+$")
RE_INDICE = re.compile(r"^codex-[a-z0-9_]+$")
ESTADOS = {"activo", "retirado"}


class DeltaError(Exception):
    """Delta rechazado. Nada se ha escrito."""


# ---------------------------------------------------------------- clientes ES

class EsReal:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def _pide(self, metodo, ruta, cuerpo=None):
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(
            f"{self.url}{ruta}", data=datos, method=metodo,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                cruda = r.read().decode()
                return json.loads(cruda) if cruda else {}
        except urllib.error.HTTPError as e:
            cuerpo_err = e.read().decode()
            if e.code == 404:
                return None
            raise DeltaError(f"ES {e.code} en {metodo} {ruta}: {cuerpo_err[:400]}")
        except urllib.error.URLError as e:
            raise DeltaError(f"No hay conexion con ES en {self.url}: {e.reason}")

    def cluster_uuid(self):
        r = self._pide("GET", "/")
        return (r or {}).get("cluster_uuid", "desconocido")

    def instala_plantilla(self, nombre, cuerpo):
        return self._pide("PUT", f"/_index_template/{nombre}", cuerpo)

    def existe_indice(self, indice):
        return self._pide("GET", f"/{indice}") is not None

    def crea_indice(self, indice):
        self._pide("PUT", f"/{indice}", {})

    def lee(self, indice, _id):
        codificado = urllib.parse.quote(_id, safe="")
        r = self._pide("GET", f"/{indice}/_doc/{codificado}")
        return r["_source"] if r and r.get("found") else None

    def escribe(self, indice, _id, cuerpo):
        codificado = urllib.parse.quote(_id, safe="")
        self._pide("PUT", f"/{indice}/_doc/{codificado}", cuerpo)

    def refresca(self, indice):
        self._pide("POST", f"/{indice}/_refresh")

    def todo(self, indice):
        r = self._pide("POST", f"/{indice}/_search",
                       {"query": {"match_all": {}}, "size": 10000})
        if not r:
            return []
        return [h["_source"] for h in r["hits"]["hits"]]


class EsFalso:
    """ES en memoria. Misma interfaz. Solo para la suite de pruebas."""

    def __init__(self):
        self.indices = {}
        self.plantilla = None

    def cluster_uuid(self):
        return "cluster-de-mentira"

    def instala_plantilla(self, nombre, cuerpo):
        self.plantilla = cuerpo
        return {"acknowledged": True}

    def existe_indice(self, indice):
        return indice in self.indices

    def crea_indice(self, indice):
        self.indices.setdefault(indice, {})

    def lee(self, indice, _id):
        doc = self.indices.get(indice, {}).get(_id)
        return json.loads(json.dumps(doc)) if doc else None

    def escribe(self, indice, _id, cuerpo):
        if indice not in self.indices:
            raise DeltaError(f"indice inexistente: {indice}")
        campos_validos = set(
            json.loads(TEMPLATE.read_text())["template"]["mappings"]["properties"])
        sobran = set(cuerpo) - campos_validos
        if sobran:
            raise DeltaError(
                f"mapping strict: campo desconocido {sorted(sobran)} en {indice}/{_id}")
        self.indices[indice][_id] = json.loads(json.dumps(cuerpo))

    def refresca(self, indice):
        pass

    def todo(self, indice):
        return [json.loads(json.dumps(d)) for d in self.indices.get(indice, {}).values()]


def detecta_es():
    """Resuelve la URL de ES. La IP del contenedor no es estable, asi que se
    consulta a docker en cada arranque. ES_URL manda si esta puesta."""
    if os.environ.get("ES_URL"):
        return os.environ["ES_URL"]
    try:
        r = subprocess.run(
            ["docker", "inspect", CONTENEDOR_ES, "--format",
             "{{.NetworkSettings.Networks." + RED_ES + ".IPAddress}}"],
            capture_output=True, text=True, timeout=15)
        ip = r.stdout.strip()
        if ip:
            return f"http://{ip}:9200"
    except Exception:
        pass
    return "http://localhost:9200"


# ------------------------------------------------------------------- modelo

NIVELES = {
    "db": {
        "campo_id": None,
        "editables": {"db", "descripcion"},
        "requeridos_crear": {"db"},
    },
    "collection": {
        "campo_id": "collection_id",
        "editables": {"collection", "notas", "orden", "estado"},
        "requeridos_crear": {"collection_id", "collection"},
    },
    "doc": {
        "campo_id": "doc_id",
        "editables": {"doc", "concepto", "fecha", "orden", "estado", "collection_id"},
        "requeridos_crear": {"doc_id", "doc", "collection_id"},
    },
    "bloque": {
        "campo_id": "bloque_id",
        "editables": {"bloque", "contenido", "fecha", "orden", "estado",
                      "collection_id", "doc_id"},
        "requeridos_crear": {"bloque_id", "bloque", "contenido",
                             "collection_id", "doc_id"},
    },
}

OPS = {
    "crear_db":            ("db", "crear"),
    "editar_db":           ("db", "editar"),
    "crear_collection":    ("collection", "crear"),
    "editar_collection":   ("collection", "editar"),
    "crear_doc":           ("doc", "crear"),
    "editar_doc":          ("doc", "editar"),
    "crear_bloque":        ("bloque", "crear"),
    "editar_bloque":       ("bloque", "editar"),
    "retirar_bloque":      ("bloque", "retirar"),
    "restaurar_bloque":    ("bloque", "restaurar"),
    "mover_bloque":        ("bloque", "mover"),
}

META = {"op", "index", "version"}


def clave(tipo, op):
    if tipo == "db":
        return "db"
    return f"{tipo}:{op[NIVELES[tipo]['campo_id']]}"


def ahora():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- validacion

def planifica(delta, es):
    """Valida el delta ENTERO contra el estado actual de ES y devuelve la lista
    de escrituras. Si algo falla lanza DeltaError y no se escribe nada.

    Devuelve (escrituras, indices_a_crear, indices_tocados)."""

    if not isinstance(delta, dict):
        raise DeltaError("el delta debe ser un objeto JSON")
    delta_id = delta.get("delta_id")
    if not delta_id or not isinstance(delta_id, str):
        raise DeltaError("falta delta_id")
    ops = delta.get("ops")
    if not isinstance(ops, list) or not ops:
        raise DeltaError("falta la lista 'ops' o esta vacia")

    escrituras = []
    crear_indices = []
    tocados = set()
    # estado simulado: lo que ya existiria si las ops anteriores del mismo
    # delta se hubiesen aplicado. Permite crear un doc y sus bloques en un solo
    # delta sin que la validacion se queje.
    simulado = {}

    def leer(indice, _id):
        if (indice, _id) in simulado:
            return simulado[(indice, _id)]
        if not es.existe_indice(indice):
            return None
        return es.lee(indice, _id)

    for i, op in enumerate(ops, 1):
        pos = f"op {i} ({op.get('op', '?')})"
        try:
            _valida_op(op, delta_id, es, leer, simulado, escrituras,
                       crear_indices, tocados)
        except DeltaError as e:
            raise DeltaError(f"{pos}: {e}")

    return escrituras, crear_indices, sorted(tocados)


def _valida_op(op, delta_id, es, leer, simulado, escrituras, crear_indices, tocados):
    if not isinstance(op, dict):
        raise DeltaError("no es un objeto")
    nombre = op.get("op")
    if nombre not in OPS:
        raise DeltaError(f"operacion desconocida '{nombre}'. "
                         f"Validas: {', '.join(sorted(OPS))}")
    tipo, modo = OPS[nombre]
    nivel = NIVELES[tipo]

    indice = op.get("index")
    if not indice or not RE_INDICE.match(indice):
        raise DeltaError(f"'index' invalido: {indice!r}. "
                         "Debe ser codex-<slug> en minusculas, digitos y _")
    hist = indice + "-hist"

    version = op.get("version")
    if not isinstance(version, int) or version < 1:
        raise DeltaError("'version' debe ser un entero >= 1")

    # --- el indice existe, o lo esta creando esta misma operacion
    if nombre == "crear_db":
        if es.existe_indice(indice) or indice in crear_indices:
            raise DeltaError(f"el indice {indice} ya existe. "
                             "crear_db solo vale para DBs nuevas")
        crear_indices.append(indice)
    else:
        if not es.existe_indice(indice) and indice not in crear_indices:
            raise DeltaError(f"el indice {indice} no existe. "
                             "Solo crear_db puede crear indices")

    # --- campos permitidos
    campos = set(op) - META
    desconocidos = campos - nivel["editables"] - {nivel["campo_id"]}
    desconocidos.discard(None)
    if desconocidos:
        raise DeltaError(f"campos no permitidos para {tipo}: {sorted(desconocidos)}")

    # --- slugs
    for c in ("collection_id", "doc_id", "bloque_id"):
        if c in op and not RE_SLUG.match(str(op[c])):
            raise DeltaError(f"{c}={op[c]!r} no es un slug valido "
                             "(minusculas, digitos y _)")

    if "estado" in op and op["estado"] not in ESTADOS:
        raise DeltaError(f"estado invalido {op['estado']!r}. "
                         f"Validos: {sorted(ESTADOS)}")

    if "orden" in op and not isinstance(op["orden"], int):
        raise DeltaError("'orden' debe ser entero")

    if tipo != "db" and nivel["campo_id"] not in op:
        raise DeltaError(f"falta '{nivel['campo_id']}'")

    _id = clave(tipo, op)
    actual = leer(indice, _id)

    # --- existencia y version
    if modo == "crear":
        if actual is not None:
            raise DeltaError(f"{_id} ya existe (va por la version "
                             f"{actual['version']}). Usa editar_{tipo}")
        if version != 1:
            raise DeltaError(f"al crear, version debe ser 1 (llego {version})")
        faltan = nivel["requeridos_crear"] - set(op)
        if faltan:
            raise DeltaError(f"faltan campos obligatorios: {sorted(faltan)}")
        base = {}
    else:
        if actual is None:
            raise DeltaError(f"{_id} no existe en {indice}")
        esperada = actual["version"] + 1
        if version != esperada:
            raise DeltaError(f"salto de version: {_id} va por la "
                             f"{actual['version']}, esperaba {esperada}, "
                             f"llego {version}")
        base = dict(actual)

    # --- reglas propias de cada modo
    if modo == "retirar":
        if base.get("estado") != "activo":
            raise DeltaError(f"solo se retira lo activo (esta {base.get('estado')})")
        op = dict(op, estado="retirado")
    elif modo == "restaurar":
        if base.get("estado") != "retirado":
            raise DeltaError(f"solo se restaura lo retirado (esta {base.get('estado')})")
        op = dict(op, estado="activo")
    elif modo == "mover":
        if "collection_id" not in op and "doc_id" not in op:
            raise DeltaError("mover_bloque necesita collection_id y/o doc_id")

    # --- integridad referencial: el padre debe existir y no estar retirado
    if tipo in ("doc", "bloque"):
        cid = op.get("collection_id", base.get("collection_id"))
        padre = leer(indice, f"collection:{cid}")
        if padre is None:
            raise DeltaError(f"la collection '{cid}' no existe en {indice}")
    if tipo == "bloque":
        did = op.get("doc_id", base.get("doc_id"))
        padre = leer(indice, f"doc:{did}")
        if padre is None:
            raise DeltaError(f"el doc '{did}' no existe en {indice}")
        if padre.get("collection_id") != op.get("collection_id",
                                                base.get("collection_id")):
            raise DeltaError(
                f"el doc '{did}' vive en la collection "
                f"'{padre.get('collection_id')}', no en "
                f"'{op.get('collection_id', base.get('collection_id'))}'")

    # --- construccion del documento completo
    nuevo = dict(base)
    for c, v in op.items():
        if c not in META:
            nuevo[c] = v
    nuevo["tipo"] = tipo
    nuevo["version"] = version
    nuevo["origen"] = delta_id
    nuevo["creado_en"] = ahora()
    if tipo != "db":
        nuevo.setdefault("estado", "activo")
        nuevo.setdefault("orden", 10)

    # --- no se retira un contenedor que aun tiene hijos activos: desaparecerian
    #     del .md sin que nadie los haya retirado.
    if (tipo in ("collection", "doc")
            and nuevo.get("estado") == "retirado"
            and base.get("estado") != "retirado"):
        vivos = _todo_con_simulado(indice, es, simulado)
        if tipo == "collection":
            hijos = [d for d in vivos if d.get("tipo") == "doc"
                     and d.get("collection_id") == op[nivel["campo_id"]]
                     and d.get("estado") == "activo"]
            que = "docs"
        else:
            hijos = [b for b in vivos if b.get("tipo") == "bloque"
                     and b.get("doc_id") == op[nivel["campo_id"]]
                     and b.get("estado") == "activo"]
            que = "bloques"
        if hijos:
            raise DeltaError(
                f"no se puede retirar: quedan {len(hijos)} {que} activos dentro. "
                "Retiralos primero, en este mismo delta si quieres")

    simulado[(indice, _id)] = nuevo
    escrituras.append((indice, _id, nuevo))
    escrituras.append((hist, f"{_id}@v{version}", nuevo))
    tocados.add(indice)


def _todo_con_simulado(indice, es, simulado):
    """El contenido del indice tal y como quedaria si las ops anteriores de
    este mismo delta ya se hubiesen aplicado."""
    base = {}
    if es.existe_indice(indice):
        for d in es.todo(indice):
            k = clave(d["tipo"], d) if d["tipo"] != "db" else "db"
            base[k] = d
    for (ix, _id), doc in simulado.items():
        if ix == indice:
            base[_id] = doc
    return list(base.values())


# ---------------------------------------------------------------- aplicacion

def aplica(escrituras, crear_indices, es):
    for indice in crear_indices:
        es.crea_indice(indice)
        es.crea_indice(indice + "-hist")
    hechas = []
    for indice, _id, cuerpo in escrituras:
        try:
            es.escribe(indice, _id, cuerpo)
            hechas.append((indice, _id))
        except DeltaError as e:
            raise DeltaError(
                f"fallo a mitad de escritura en {indice}/{_id}: {e}\n"
                f"  APLICADAS ANTES DEL FALLO: {hechas}\n"
                f"  Esto no se revierte solo. Revisa a mano antes de reintentar.")
    return hechas


# -------------------------------------------------------------- concatenador

def _fecha(v):
    return v if v else "—"


def concatena(indice, es, delta_id=None):
    """Lee el indice VIVO y devuelve el .md. Salta lo retirado."""
    docs = es.todo(indice)
    if not docs:
        return None

    db = next((d for d in docs if d["tipo"] == "db"), None)
    if db is None:
        raise DeltaError(f"{indice} no tiene documento tipo 'db'")

    cols = sorted([d for d in docs if d["tipo"] == "collection"
                   and d.get("estado") != "retirado"],
                  key=lambda d: (d.get("orden", 10), d.get("collection", "")))
    docs_ = [d for d in docs if d["tipo"] == "doc" and d.get("estado") != "retirado"]
    blqs = [d for d in docs if d["tipo"] == "bloque" and d.get("estado") != "retirado"]

    ultima = max((d.get("creado_en", "") for d in docs), default="")[:10]

    L = []
    sello = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    tras = f" · tras {delta_id}" if delta_id else ""
    L.append(f"*Generado por daemon-delta · {sello}{tras}*")
    L.append("")
    L.append(f"# DB: {db['db']}")
    L.append("")
    L.append(f"**Descripción:** {_fecha(db.get('descripcion'))}")
    L.append(f"**Última Actualización:** {ultima or '—'}")
    L.append("")

    for col in cols:
        L.append("---")
        L.append("")
        L.append(f"## COLLECTION: {col['collection']}")
        L.append("")
        if col.get("notas"):
            L.append(f"*(Notas de la colección: {col['notas']})*")
            L.append("")

        mios = sorted([d for d in docs_ if d.get("collection_id") == col["collection_id"]],
                      key=lambda d: (d.get("orden", 10), d.get("doc", "")))
        for d in mios:
            L.append(f"### DOC: {d['doc']}")
            L.append("")
            L.append(f"* **Estado:** [{d.get('estado', 'activo').capitalize()}]")
            L.append(f"* **Fecha:** {_fecha(d.get('fecha'))}")
            L.append(f"* **Concepto:** {_fecha(d.get('concepto'))}")
            L.append("")

            suyos = sorted([b for b in blqs if b.get("doc_id") == d["doc_id"]],
                           key=lambda b: (b.get("orden", 10), b.get("bloque", "")))
            for b in suyos:
                L.append(f"**· Bloque: {b['bloque']}** "
                         f"*(fecha: {_fecha(b.get('fecha'))} "
                         f"Estado: [{b.get('estado', 'activo').capitalize()}])*  ")
                L.append(b.get("contenido", ""))
                L.append("")

    return "\n".join(L).rstrip() + "\n"


# ------------------------------------------------------------------- ledger

def transferencia_acabada(ruta, intentos=5):
    """Patron copiado del daemon de N1: esperar a que el tamano deje de
    cambiar. La disciplina es crear el .json fuera y moverlo a inbox/, pero
    mover entre carpetas de Windows no es atomico visto desde Linux."""
    anterior = -1
    for _ in range(intentos):
        try:
            actual = ruta.stat().st_size
        except FileNotFoundError:
            return False
        if actual == anterior and actual > 0:
            return True
        anterior = actual
        time.sleep(1)
    return False


def lee_ledger():
    if REGISTRO.exists():
        try:
            return json.loads(REGISTRO.read_text())
        except json.JSONDecodeError:
            log("registro.json corrupto, empiezo de cero")
    return {"aplicados": {}, "cluster_uuid": None}


def guarda_ledger(led):
    """Rotacion copiada de maybe_rotate_state del daemon de N1."""
    REGISTRO_DIR.mkdir(parents=True, exist_ok=True)
    if len(led["aplicados"]) > REGISTRO_MAX:
        sello = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        viejos = dict(sorted(led["aplicados"].items(),
                             key=lambda kv: kv[1]["cuando"])[:-REGISTRO_MAX // 2])
        (REGISTRO_DIR / f"registro-{sello}.json").write_text(
            json.dumps(viejos, indent=2, ensure_ascii=False), encoding="utf-8")
        for k in viejos:
            del led["aplicados"][k]
        log(f"registro rotado: {len(viejos)} entradas a registro-{sello}.json")
    REGISTRO.write_text(json.dumps(led, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def escribe_acuse(ruta, cuerpo):
    """El acuse de recibo del tool calling: un .json por delta con lo que paso.
    Hoy lo lees tu; el Kairos automatico lo leera solo."""
    ACUSES.mkdir(parents=True, exist_ok=True)
    destino = ACUSES / (ruta.stem + ".acuse.json")
    destino.write_text(json.dumps(cuerpo, indent=2, ensure_ascii=False),
                       encoding="utf-8")
    return destino


def log(msg):
    sello = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{sello}] {msg}", flush=True)


# ------------------------------------------------------------------ comandos

def cmd_init(es):
    cuerpo = json.loads(TEMPLATE.read_text())
    r = es.instala_plantilla("codex-tpl", cuerpo)
    print("plantilla codex-tpl instalada:", r)


def procesa_uno(ruta, es, led, dry):
    """Devuelve (aplicado?, error). aplicado=None significa 'saltado'."""
    log(f"── {ruta.name}")
    acuse = {"fichero": ruta.name, "delta_id": None,
             "cuando": ahora(), "resultado": None, "error": None}

    if not dry and not transferencia_acabada(ruta):
        return False, "el fichero sigue creciendo o desaparecio"

    try:
        delta = json.loads(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        acuse.update(resultado="rechazado", error=f"JSON invalido: {e}")
        escribe_acuse(ruta, acuse)
        return False, f"JSON invalido: {e}"

    delta_id = delta.get("delta_id") if isinstance(delta, dict) else None
    acuse["delta_id"] = delta_id

    if delta_id and delta_id in led["aplicados"]:
        prev = led["aplicados"][delta_id]
        log(f"   ya aplicado el {prev['cuando']} — se salta (idempotencia)")
        acuse.update(resultado="saltado",
                     error=f"delta_id ya aplicado el {prev['cuando']}")
        escribe_acuse(ruta, acuse)
        return None, None

    try:
        escrituras, crear, tocados = planifica(delta, es)
    except DeltaError as e:
        acuse.update(resultado="rechazado", error=str(e))
        escribe_acuse(ruta, acuse)
        return False, str(e)

    log(f"   {len(escrituras) // 2} operaciones validadas, "
        f"{len(escrituras)} escrituras, indices: {', '.join(tocados)}")
    if dry:
        for indice, _id, _ in escrituras:
            log(f"     · {indice}/{_id}")
        return None, None

    try:
        aplica(escrituras, crear, es)
    except DeltaError as e:
        acuse.update(resultado="rechazado", error=str(e))
        escribe_acuse(ruta, acuse)
        return False, str(e)

    mds = []
    for indice in tocados:
        es.refresca(indice)
        md = concatena(indice, es, delta_id)
        if md:
            OUT.mkdir(parents=True, exist_ok=True)
            destino = OUT / f"{indice.removeprefix('codex-')}.md"
            destino.write_text(md, encoding="utf-8")
            mds.append(str(destino))
            log(f"   .md -> {destino}")

    versiones = {_id: c["version"] for ix, _id, c in escrituras
                 if not ix.endswith("-hist")}
    acuse.update(resultado="aplicado", ops=len(delta["ops"]),
                 escrituras=len(escrituras), indices=tocados,
                 md_generados=mds, versiones=versiones)
    escribe_acuse(ruta, acuse)
    led["aplicados"][delta_id] = {"cuando": ahora(), "fichero": ruta.name,
                                  "ops": len(delta["ops"]),
                                  "indices": tocados}
    return True, None


def guarda_cluster(led, es):
    """El registro dice que un delta ya se aplico. Pero se aplico A UN CLUSTER
    CONCRETO. Si ES se recrea desde cero, el registro mentiria y saltaria
    deltas que este ES nunca vio. Anclarlo al uuid lo hace detectable."""
    uuid = es.cluster_uuid()
    if led.get("cluster_uuid") is None:
        led["cluster_uuid"] = uuid
        return None
    if led["cluster_uuid"] != uuid:
        return (f"El registro pertenece al cluster {led['cluster_uuid']} y este "
                f"es {uuid}.\nES se ha recreado desde cero: hay "
                f"{len(led['aplicados'])} deltas marcados como aplicados que "
                "este cluster NUNCA vio.\nPara rehacer el estado desde los "
                "deltas:\n    python3 daemon_delta.py reconstruir")
    return None


def una_pasada(es, led, dry=False):
    """Un barrido de inbox/. Devuelve (procesados, rechazados)."""
    ficheros = sorted(INBOX.glob("*.json"))
    if not ficheros:
        return 0, 0
    hechos = fallos = 0
    for ruta in ficheros:
        ok, err = procesa_uno(ruta, es, led, dry)
        if dry:
            continue
        if ok is None:
            shutil.move(str(ruta), APPLIED / ruta.name)
            hechos += 1
            continue
        if ok:
            shutil.move(str(ruta), APPLIED / ruta.name)
            hechos += 1
        else:
            fallos += 1
            log(f"   RECHAZADO: {err}")
            shutil.move(str(ruta), FAILED / ruta.name)
            (FAILED / (ruta.stem + ".error.txt")).write_text(err, encoding="utf-8")
    if not dry:
        guarda_ledger(led)
    return hechos, fallos


def prepara(es):
    for d in CARPETAS:
        d.mkdir(parents=True, exist_ok=True)
    led = lee_ledger()
    aviso = guarda_cluster(led, es)
    if aviso:
        log("*** REGISTRO Y CLUSTER NO CUADRAN ***\n" + aviso)
        return None
    return led


def cmd_run(es, dry=False):
    led = prepara(es)
    if led is None:
        return 2
    hechos, fallos = una_pasada(es, led, dry)
    if hechos == 0 and fallos == 0:
        log("inbox vacio")
    else:
        log(f"hecho. procesados: {hechos}  rechazados: {fallos}")
    return 1 if fallos else 0


def cmd_vigilar(es):
    """Bucle de sondeo, mismo patron que el daemon de N1: las notificaciones
    del kernel no cruzan a /mnt/d, asi que se lista la carpeta y se compara.
    En serie a proposito: dos deltas en paralelo sobre el mismo bloque se
    pisarian las versiones."""
    led = prepara(es)
    if led is None:
        return 2
    log(f"vigilando {INBOX}  (cada {ESPERA_LARGA}s en reposo, "
        f"{ESPERA_CORTA}s con trabajo)")
    log(f"ES: {es.url}   salida: {OUT}   acuses: {ACUSES}")
    log("Ctrl+C para parar")
    try:
        while True:
            hechos, fallos = una_pasada(es, led)
            time.sleep(ESPERA_CORTA if (hechos or fallos) else ESPERA_LARGA)
    except KeyboardInterrupt:
        log("daemon detenido.")
        return 0


def cmd_reconstruir(es):
    """Axioma 4 de CN46: ES es estado derivado. Esto lo demuestra: borra el
    registro y reejecuta TODO applied/ en orden de nombre contra el ES actual."""
    for d in CARPETAS:
        d.mkdir(parents=True, exist_ok=True)
    ficheros = sorted(APPLIED.glob("*.json"))
    if not ficheros:
        log("no hay nada en applied/ que reconstruir")
        return 1
    log(f"reconstruyendo desde {len(ficheros)} deltas de applied/")
    guarda_ledger({"aplicados": {}, "cluster_uuid": es.cluster_uuid()})
    for ruta in ficheros:
        shutil.move(str(ruta), INBOX / ruta.name)
    return cmd_run(es)


def cmd_md(indice, es):
    md = concatena(indice, es)
    if md is None:
        print(f"{indice} vacio o inexistente")
        return 1
    OUT.mkdir(exist_ok=True)
    destino = OUT / f"{indice.removeprefix('codex-')}.md"
    destino.write_text(md, encoding="utf-8")
    print(f"-> {destino}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("comando",
                   choices=["init", "run", "vigilar", "md",
                            "reconstruir"])
    p.add_argument("indice", nargs="?")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--es", help="URL de ES (por defecto: autodetecta el contenedor)")
    a = p.parse_args()


    es = EsReal(a.es or detecta_es())
    print(f"ES: {es.url}")
    if a.comando == "init":
        cmd_init(es)
        return 0
    if a.comando == "run":
        return cmd_run(es, a.dry_run)
    if a.comando == "vigilar":
        return cmd_vigilar(es)
    if a.comando == "reconstruir":
        return cmd_reconstruir(es)
    if a.comando == "md":
        if not a.indice:
            p.error("md necesita el nombre del indice")
        return cmd_md(a.indice, es)


if __name__ == "__main__":
    sys.exit(main() or 0)
