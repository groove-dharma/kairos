#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
muestras.py — genera las muestras que consume el piloto. UN SOLO SCRIPT.

Antes hacian falta tres pasos: build_corpus.py para bajar las notas, una
consulta a Postgres para sacar fechas.csv, y el muestreador. Y fechas.csv
guardaba solo el trimestre, asi que no se podia pedir un mes.

Esto hace las tres cosas. La fecha sale de la propia API: la vista de lista de
/notes devuelve created_at_utc (verificado en notes.rs, NoteSummary), asi que
no hay que tocar Postgres ni mantener un CSV aparte.

La cache es incremental: la primera vez baja todo, las siguientes solo lo que
falte. Con --sin-actualizar no toca la red.

FORMATOS DE PERIODO
-------------------
    2026            un año entero
    2026-06         un mes
    2026-Q2         un trimestre
    2026-S1         un semestre (S1 = enero-junio, S2 = julio-diciembre)
    2026-06 2026-12 un rango: del principio del primero al final del ultimo.
                    Vale mezclar granularidades (2026-Q1 2026-08).
    random          todo el corpus, sin filtro de fecha

EJEMPLOS
    python muestras.py --que-hay                 # inventario, sin generar nada
    python muestras.py --periodo 2026-Q3
    python muestras.py --periodo 2026-06 2026-12
    python muestras.py --periodo random --nombre corpus-entero
    python muestras.py --periodo 2026-Q3 --sin-actualizar
"""

from __future__ import annotations
import argparse, json, os, random, re, sys, time
from collections import defaultdict

TOPE = 450       # trozos por muestra: lo que aguanta una corrida
GIGANTE = 400    # notas que sobrepasan esto se apartan
SEMILLA = 42     # la misma peticion da siempre la misma muestra

CACHE = "/app/corpus/corpus.jsonl"
SALIDA = "/app/corpus/muestras"
API = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000") + "/api/v1"
PAGINA = 100

# Tags de las notas que produce el propio pipeline. NO se muestrean: analizar
# las hipotesis que el sistema escribio seria hacerle leer sus propias
# conclusiones como si fueran material del corpus, y con `random` saldrian
# seguro. Se excluyen al listar, sin coste: la vista de lista ya trae los tags.
# Las que ESCRIBIO el pipeline: son sus conclusiones, no material del corpus.
TAGS_DEL_SISTEMA = ("thesis",)
PREFIJOS_DEL_SISTEMA = ("h-", "estado/", "absorbida-en/")

# `cited/h-NNN` es distinto: son notas TUYAS, el tag solo dice que alguna
# hipotesis las cito como evidencia. Excluirlas quitaria material legitimo del
# corpus -- y justo el mas cargado, porque fue el que sostuvo una hipotesis. Por
# defecto se quedan; --sin-citadas las saca.

_DIAS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


# ══════════════════════════════════════════════════════════════════════════════
# PERIODOS
# ══════════════════════════════════════════════════════════════════════════════

def _ultimo_dia(anio: int, mes: int) -> int:
    if mes == 2 and not (anio % 4 == 0 and (anio % 100 or anio % 400 == 0)):
        return 28
    return _DIAS[mes - 1]


def limites(periodo: str) -> tuple[str, str, str]:
    """De un periodo a (desde, hasta, nombre_de_archivo), en YYYY-MM-DD."""
    p = str(periodo).strip().upper()

    m = re.fullmatch(r"(\d{4})", p)
    if m:
        a = m.group(1)
        return f"{a}-01-01", f"{a}-12-31", a

    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m:
        a, mes = int(m.group(1)), int(m.group(2))
        if not 1 <= mes <= 12:
            raise ValueError(f"mes fuera de rango: {periodo}")
        return (f"{a}-{mes:02d}-01",
                f"{a}-{mes:02d}-{_ultimo_dia(a, mes):02d}",
                f"{a}-{mes:02d}")

    m = re.fullmatch(r"(\d{4})-Q([1-4])", p)
    if m:
        a, q = int(m.group(1)), int(m.group(2))
        ini, fin = (q - 1) * 3 + 1, (q - 1) * 3 + 3
        return (f"{a}-{ini:02d}-01",
                f"{a}-{fin:02d}-{_ultimo_dia(a, fin):02d}",
                f"{a}-Q{q}")

    m = re.fullmatch(r"(\d{4})-S([12])", p)
    if m:
        a, s = int(m.group(1)), int(m.group(2))
        if s == 1:
            return f"{a}-01-01", f"{a}-06-30", f"{a}-S1"
        return f"{a}-07-01", f"{a}-12-31", f"{a}-S2"

    raise ValueError(
        f"no entiendo '{periodo}'. Formatos: 2026, 2026-06, 2026-Q2, "
        f"2026-S1, o random.")


def resolver(periodos: list) -> tuple:
    """Uno o varios periodos -> (desde, hasta, nombre). Varios = rango."""
    if len(periodos) == 1 and str(periodos[0]).strip().lower() == "random":
        return None, None, "random"
    trozos = [limites(p) for p in periodos]
    desde = min(t[0] for t in trozos)
    hasta = max(t[1] for t in trozos)
    nombre = (trozos[0][2] if len(trozos) == 1
              else f"{trozos[0][2]}_a_{trozos[-1][2]}")
    return desde, hasta, nombre


# ══════════════════════════════════════════════════════════════════════════════
# CACHE DEL CORPUS  (id, texto y FECHA en el mismo sitio)
# ══════════════════════════════════════════════════════════════════════════════

def _get(sesion, url, params=None, reintentos=3):
    for intento in range(reintentos):
        try:
            r = sesion.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except Exception:
            if intento == reintentos - 1:
                return None
        time.sleep(1 + intento * 2)
    return None


def es_del_sistema(tags, sin_citadas: bool = False) -> bool:
    """Si la nota la escribio el pipeline y no debe muestrearse."""
    for x in (tags or []):
        n = str(x.get("name", x) if isinstance(x, dict) else x).strip().lower()
        if n in TAGS_DEL_SISTEMA or n.startswith(PREFIJOS_DEL_SISTEMA):
            return True
        if sin_citadas and n.startswith("cited/"):
            return True
    return False


def leer_cache(ruta: str) -> dict:
    """id -> registro. Tolera el formato viejo, que no traia fecha."""
    notas = {}
    if not os.path.exists(ruta):
        return notas
    with open(ruta, encoding="utf-8") as fh:
        for linea in fh:
            try:
                r = json.loads(linea)
            except Exception:
                continue
            if r.get("id"):
                notas[r["id"]] = r
    return notas


def actualizar(ruta: str, notas: dict, sin_citadas: bool = False) -> dict:
    """Baja lo que falte de Fortemi. Devuelve el diccionario actualizado.

    La fecha viene de la MISMA llamada que lista las notas: la vista de lista
    de /notes incluye created_at_utc. Por eso no hace falta Postgres ni un
    fechas.csv aparte, que ademas solo guardaba el trimestre y por eso no se
    podia pedir un mes.

    Listar es barato (una llamada por cada 100 notas). Bajar el texto es lo
    caro: una llamada por nota. Por eso solo se baja el texto que falta, pero
    las fechas se refrescan siempre.
    """
    try:
        import requests
    except ImportError:
        print("AVISO: no hay `requests` disponible. Se usa la cache tal cual.")
        return notas

    sesion = requests.Session()
    print(f"consultando {API} ...")

    fechas, ids, offset = {}, [], 0
    del_sistema = set()
    while True:
        d = _get(sesion, f"{API}/notes",
                 params={"limit": PAGINA, "offset": offset,
                         "sort_by": "created_at", "sort_order": "asc"})
        if not d:
            break
        lote = d.get("notes") or []
        if not lote:
            break
        for n in lote:
            nid = n.get("id")
            if not nid:
                continue
            if es_del_sistema(n.get("tags"), sin_citadas):
                del_sistema.add(nid)
                continue
            ids.append(nid)
            f = str(n.get("created_at_utc") or "")[:10]
            if f:
                fechas[nid] = f
        if len(lote) < PAGINA:
            break
        offset += PAGINA
        if offset % 1000 == 0:
            print(f"  listadas {len(ids):,}", flush=True)
    if not ids:
        print("  no se pudo listar (Fortemi caido?). Se usa la cache tal cual.")
        return notas
    print(f"  {len(ids):,} notas en Fortemi"
          + (f" ({len(del_sistema):,} del pipeline, excluidas)" if del_sistema else ""))

    # Si una nota del sistema entro en la cache antes de este filtro, se saca.
    fuera = [nid for nid in del_sistema if nid in notas]
    for nid in fuera:
        notas.pop(nid, None)
    if fuera:
        print(f"  {len(fuera):,} nota(s) del pipeline sacadas de la cache")

    puestas = 0
    for nid, f in fechas.items():
        if nid in notas and notas[nid].get("fecha") != f:
            notas[nid]["fecha"] = f
            puestas += 1
    if puestas:
        print(f"  {puestas:,} fecha(s) puestas o corregidas")

    faltan = [n for n in ids if n not in notas or not notas[n].get("text")]
    if not faltan:
        print("  nada nuevo que bajar")
    else:
        print(f"  bajando {len(faltan):,} nota(s) nueva(s)")
        t0, vacias = time.time(), 0
        for i, nid in enumerate(faltan, 1):
            d = _get(sesion, f"{API}/notes/{nid}")
            if not d:
                continue
            texto = ((d.get("original") or {}).get("content")
                     or (d.get("revised") or {}).get("content") or "").strip()
            if not texto:
                vacias += 1
                continue
            notas[nid] = {"id": nid, "text": texto, "speaker": "author",
                          "fecha": fechas.get(nid, "")}
            if i % 100 == 0:
                v = i / max(time.time() - t0, 0.01)
                print(f"    [{i:,}/{len(faltan):,}] {v:.1f}/s  "
                      f"ETA {(len(faltan) - i) / max(v, 0.01) / 60:.1f} min",
                      flush=True)
        if vacias:
            print(f"  {vacias:,} sin texto, omitidas")

    tmp = ruta + ".tmp"
    carpeta = os.path.dirname(ruta)
    if carpeta:
        os.makedirs(carpeta, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        for r in notas.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, ruta)
    print(f"  cache: {len(notas):,} notas en {ruta}")
    return notas


# ══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Genera la muestra de un periodo. Baja de Fortemi lo que "
                    "falte y solo escribe el periodo pedido.")
    ap.add_argument("--periodo", nargs="*", default=[],
                    help="2026 | 2026-06 | 2026-Q2 | 2026-S1 | random. "
                         "Dos valores = rango.")
    ap.add_argument("--que-hay", action="store_true",
                    help="Solo el inventario por trimestre, sin generar nada")
    ap.add_argument("--todos", action="store_true",
                    help="Una muestra por cada trimestre con notas")
    ap.add_argument("--nombre", default="", help="Nombre del .jsonl")
    ap.add_argument("--tope", type=int, default=TOPE,
                    help=f"Trozos por muestra (por defecto {TOPE})")
    ap.add_argument("--gigante", type=int, default=GIGANTE,
                    help=f"Notas con mas trozos se apartan (por defecto {GIGANTE})")
    ap.add_argument("--semilla", type=int, default=SEMILLA)
    ap.add_argument("--sin-citadas", action="store_true",
                    help="Excluir tambien las notas citadas como evidencia "
                         "por alguna hipotesis (por defecto SI se muestrean: "
                         "son notas tuyas)")
    ap.add_argument("--sin-actualizar", action="store_true",
                    help="No consultar Fortemi; usar la cache tal como esta")
    ap.add_argument("--cache", default=CACHE)
    ap.add_argument("--salida", default=SALIDA)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.periodo and not args.todos and not args.que_hay:
        ap.error("dime que periodo, o usa --todos o --que-hay")

    from gtflow.utils.text_utils import split_paragraph
    random.seed(args.semilla)

    notas = leer_cache(args.cache)
    if notas:
        print(f"cache: {len(notas):,} notas")
    if not args.sin_actualizar:
        notas = actualizar(args.cache, notas, args.sin_citadas)
    elif not notas:
        print(f"ERROR: no hay cache en {args.cache} y se pidio --sin-actualizar.")
        return 1
    if not notas:
        print("ERROR: no hay notas con las que trabajar.")
        return 1

    sin_fecha = sum(1 for r in notas.values() if not r.get("fecha"))
    if sin_fecha:
        print(f"AVISO: {sin_fecha:,} nota(s) sin fecha. No entran en ningun "
              f"periodo (si en 'random'). Corre sin --sin-actualizar para "
              f"ponerselas.")

    print("\ntroceando ...")
    utiles, apartadas = [], 0
    for r in notas.values():
        t = (r.get("text") or "").strip()
        if not t:
            continue
        n = len(split_paragraph(t, 800))
        if n > args.gigante:
            apartadas += 1
            continue
        utiles.append(({"id": r["id"], "text": t, "speaker": "author"},
                       n, r.get("fecha", "")))
    print(f"  {len(utiles):,} utilizables · {apartadas:,} gigantes apartadas")

    por_trim = defaultdict(lambda: [0, 0])
    for _, n, f in utiles:
        if len(f) >= 7:
            k = f"{f[:4]}-Q{(int(f[5:7]) - 1) // 3 + 1}"
            por_trim[k][0] += 1
            por_trim[k][1] += n

    if args.que_hay:
        print(f"\n{'trimestre':<12}{'notas':>8}{'trozos':>10}")
        for k in sorted(por_trim):
            print(f"{k:<12}{por_trim[k][0]:>8}{por_trim[k][1]:>10}")
        return 0

    peticiones = ([([k], None) for k in sorted(por_trim)] if args.todos
                  else [(args.periodo, args.nombre or None)])

    os.makedirs(args.salida, exist_ok=True)
    print(f"\n{'muestra':<20}{'disponibles':>13}{'en muestra':>12}{'trozos':>9}")

    for req, nombre_pedido in peticiones:
        try:
            desde, hasta, nombre = resolver(req)
        except ValueError as e:
            print(f"ERROR: {e}")
            return 1
        nombre = nombre_pedido or nombre

        if desde is None:
            candidatas = [(n, c) for n, c, _ in utiles]
        else:
            # YYYY-MM-DD: el orden alfabetico ES el cronologico.
            candidatas = [(n, c) for n, c, f in utiles if f and desde <= f <= hasta]

        random.shuffle(candidatas)
        sel, tot = [], 0
        for nota, c in candidatas:
            if tot + c > args.tope:
                continue
            sel.append(nota)
            tot += c

        print(f"{nombre:<20}{len(candidatas):>13}{len(sel):>12}{tot:>9}")
        if not sel:
            print("  sin notas en ese rango. No se escribe nada.")
            continue
        if args.dry_run:
            continue
        with open(os.path.join(args.salida, f"{nombre}.jsonl"), "w",
                  encoding="utf-8") as f:
            for s in sel:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print()
    print("nada escrito (--dry-run)." if args.dry_run
          else f"escrito en {args.salida}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
