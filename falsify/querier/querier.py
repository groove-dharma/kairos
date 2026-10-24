#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Querier — consulta Fortemi por API REST. v1.0.9.1 (04-oct-2026)

Niveles de resolución:
  1. abstract   — listado de búsqueda (seleccionar)
  2. caja       — revised con el marcador de caja (leer)
  3. original   — conversación cruda (por excepción)

Uso:
  querier.py "texto de la query" [--tags a,b] [--limit N] [--periodo ...]
                        [--modo fts|semantic|hybrid]
  querier.py --nota <id>        ficha sin cuerpo (detecta si hay caja)
  querier.py --caja <id>        imprime la caja; si no existe, la pide al
                                black boxer, espera y la imprime
  querier.py --original <id>    imprime la conversación cruda

Cambios v1.0.9.1: tolerancia a notas rotas. Hallazgo de la auditoría del
04-oct: la hidratación puede recibir status < 400 con body VACÍO — r.json()
explota con JSONDecodeError y el traceback mataba la corrida entera por
UNA nota (prótesis, reproducible ×2; forgiveness no tocó ninguna). Ahora:
en la hidratación del loop, la nota rota se omite con aviso (nid + contador
al cierre) y la búsqueda sigue; en los modos por nota, muerte limpia en una
línea con status y path. La causa server-side queda ABIERTA: sonda con el
nid del aviso (curl -w para status y tamaño; deleted_at en SQL).

Cambios v1.0.9: la base de petición al server pasa a 100 (LIMIT_BASE);
--limit pasa a ser CUPO DE MUESTRA (cuántas fichas mostrar), no el límite
del server. Regla medida 04-oct con pares espejo (prótesis/serendipia):
el limit solo fija el tamaño de la respuesta LA PRIMERA VEZ que una query
computa fresco; dentro de la ventana del caché, cualquier limit recibe
la respuesta guardada íntegra. Con base 100 constante: una llave por
término, la siembra nace ancha, y el limit del usuario deja de multiplicar
peticiones. Auditoría 04-oct (forgiveness): fresco único sin colgar, 100
hidratados, filtro honesto.

Cambios v1.0.8: --periodo SIN margen — muere el limit×5 (creaba peticiones
propias por término, cómputo fresco separado del de la búsqueda normal).

Cambios v1.0.7: --modo (default híbrido = NO se envía mode; --modo
fts|semantic lo manda tal cual al server). OJO: mode=fts honrado por el
server queda POR VERIFICAR — la corrida fts NO debe traer vecinos
semánticos (notas sin la palabra buscada en el texto).

Cambios v1.0.6: --periodo (granularidades de muestras.py). El hit de
/search no trae fecha: el filtro corre del lado del CLIENTE, sobre la
fecha que la hidratación ya trae. Verificación por construcción: todo
resultado mostrado cayó dentro; la línea final reporta cuántos. No
aplica a --json ni a los modos por nota. La query va PRIMERO y --periodo
después (nargs glotón).

Cambios v1.0.5: el stub murió — cliente del black boxer. La búsqueda
detecta conversaciones sin caja, anuncia "Se detectaron N…", encola al
daemon (POST /cajas) y muestra barra con polling a /estado cada 2s.

Cambios v1.0.4: docstring honesto — el "write /dev/stdout: broken pipe"
al cortar con | head NO es de este script: lo emite docker compose (Go)
cuando head le rompe SU pipe del host.

Cambios v1.0.3: SIGPIPE por defecto; --offset marcado como ignorado.

Comportamiento medido de /search (29-sep→04-oct-2026): la primera petición
de un término computa fresco (20s a varios minutos según el estado del
servidor; el aborto por timeout NO siembra el caché); las siguientes
reciben la respuesta guardada en ms — Y LA RECIBEN ÍNTEGRA con el tamaño
con que se sembró: el limit de la petición solo fija el tamaño en la
primera computación (regla del 04-oct, pares prótesis/serendipia). El
OFFSET SE IGNORA EN SILENCIO en la build actual (v2026.7.1): no pagina.
El ranking NO es determinista entre corridas (observado cuatro veces; el
snippet de la misma nota también varía). TIMEOUT=600 da margen; los
errores (500, timeout, sin conexión) salen limpios.

El hit de /search trae: note_id, score, snippet, title, tags, chain_info.
NO trae abstract ni document_type_name — el abstract lo aporta la
hidratación (un GET /notes/{id} por resultado).

Marcador de detección de caja: PROVISIONAL hasta que el protocolo 7.5
lo oficialice (el black boxer lo importa de AQUÍ — una sola fuente).
La clasificación de conversación es por tags de fuente.
"""

import argparse
import calendar
import json
import os
import re
import signal
import sys
import time

import requests

REST = os.environ.get("FORTEMI_REST_URL", "http://matric-api:3000").rstrip("/")
API = f"{REST}/api/v1"
MARCA_CAJA = "=== CAJA NEGRA KAIRÓS ==="
# Tags de fuente de las conversaciones IA ingestadas (censo jul-28).
FUENTES_CONV = {"chatgpt", "gemini", "google_aistudio", "tinfoil",
                "claude", "notebooklm", "google_keep"}
TIMEOUT = 600

# La base de petición: lo que SIEMPRE se pide al server, para que la
# búsqueda normal y todos los --periodo compartan la misma respuesta
# cacheada y la siembra nazca ancha. --limit del usuario es cupo de
# muestra local; solo viaja al server si pide MÁS que la base.
LIMIT_BASE = 100

# Black boxer: daemon siempre arriba en la misma red interna.
BB_URL = os.environ.get("BLACK_BOXER_URL", "http://black-boxer:3304").rstrip("/")
TOPE_POR_CAJA = 2700   # 45 min de espera por caja: red de seguridad del polling


def get(path, params=None, vacio_si_tag_inexistente=False, tolerante=False):
    """GET con manejo de los fallos conocidos del contrato Fortemi.
    tolerante=True: una respuesta sin JSON devuelve None (para la
    hidratación en serie — una nota rota no tira la búsqueda)."""
    try:
        r = requests.get(f"{API}{path}", params=params, timeout=TIMEOUT)
    except requests.exceptions.ConnectionError:
        sys.exit("Sin conexión con matric-api. ¿Está el stack arriba?")
    except requests.exceptions.ReadTimeout:
        sys.exit(f"Sin respuesta de matric-api en {TIMEOUT}s ({path}). "
                 "Si es la primera vez que se pide este término, reintenta.")
    if r.status_code >= 500:
        sys.exit(f"HTTP {r.status_code} en {path} — error interno del servidor. "
                 "Suele ser transitorio: reintenta.")
    if r.status_code == 404 and vacio_si_tag_inexistente:
        return None  # 404 "Required tag not found" = cero resultados, no error
    if r.status_code >= 400:
        sys.exit(f"HTTP {r.status_code} en {path} — {r.text[:200]}")
    try:
        return r.json()
    except requests.exceptions.JSONDecodeError:
        # 04-oct: existe en el mundo real — status < 400 con body vacío.
        if tolerante:
            return None
        sys.exit(f"HTTP {r.status_code} con body no-JSON en {path} — "
                 "respuesta rota del server para esta nota. "
                 f"Body[:200]: {r.text[:200]!r}")


def pedir_cajas(ids):
    """Cliente del black boxer: encola y espera con barra (polling cada 2s).
    Devuelve cuántas quedaron hechas del lote. Si el daemon no responde o el
    polling se corta, la cola sigue siendo del daemon: nada se pierde."""
    try:
        r = requests.post(f"{BB_URL}/cajas", json={"note_ids": ids}, timeout=10)
        if r.status_code >= 400:
            print(f"El black boxer rechazó la petición (HTTP {r.status_code}): {r.text[:120]}")
            return 0
        data = r.json()
    except requests.exceptions.RequestException:
        print("El black boxer no responde (¿está arriba?) — no se generaron; "
              "nada se perdió.")
        return 0
    total = data.get("encoladas", 0)
    if not total:
        return 0
    tope = time.time() + TOPE_POR_CAJA * max(1, total)
    hechas = 0
    try:
        while True:
            try:
                e = requests.get(f"{BB_URL}/estado",
                                 params={"ids": ",".join(ids)}, timeout=10).json()
            except requests.exceptions.RequestException:
                print("\nEl daemon dejó de responder a mitad — la cola sigue "
                      "suya; re-corre la query más tarde.")
                return hechas
            hechas = total - (e.get("pendientes") or 0)
            en_curso = str(e.get("en_curso") or "—")[:40]
            print(f"\r  cajas {hechas}/{total} · en curso: {en_curso:<40}",
                  end="", flush=True)
            if e.get("pendientes") == 0:
                print()
                return total
            if time.time() > tope:
                print(f"\n  espera cortada: quedan {e.get('pendientes')} en la "
                      "cola del daemon — re-corre la query más tarde.")
                return hechas
            time.sleep(2)
    except KeyboardInterrupt:
        print("\n  polling cortado — el daemon sigue con la cola; "
              "re-corre la query más tarde.")
        return hechas


def es_conversacion(detalle, hit):
    """ai-conversation por document_type_name si viniera; en la práctica,
    la vía real es el tag de fuente (el campo no viene ni en hit ni detalle)."""
    dt = hit.get("document_type_name")
    if not dt:
        nota = detalle.get("note", {}) if detalle else {}
        dt = nota.get("document_type_name")
    if dt:
        return dt == "ai-conversation"
    tags = {t.lower() for t in detalle.get("tags", [])} if detalle else set()
    return bool(tags & FUENTES_CONV)


def tiene_caja(detalle):
    revised = (detalle.get("revised") or {}).get("content") or ""
    return MARCA_CAJA in revised


# ─── Períodos (granularidades de muestras.py, filtro del lado del cliente) ───

def _granulo(v):
    """Un valor de período → (desde, hasta, etiqueta). Días YYYY-MM-DD."""
    v = v.strip()
    if re.fullmatch(r"\d{4}", v):                        # año entero
        return f"{v}-01-01", f"{v}-12-31", v
    if re.fullmatch(r"\d{4}-\d{2}", v):                  # un mes
        anio, mes = int(v[:4]), int(v[5:7])
        if not 1 <= mes <= 12:
            sys.exit(f"--periodo: mes inválido en {v!r} (01-12).")
        fin = calendar.monthrange(anio, mes)[1]
        return f"{v}-01", f"{v}-{fin:02d}", v
    if m := re.fullmatch(r"(\d{4})-Q([1-4])", v, re.I):  # un trimestre
        anio, q = int(m.group(1)), int(m.group(2))
        mes_ini, mes_fin = (q - 1) * 3 + 1, q * 3
        return (f"{anio}-{mes_ini:02d}-01",
                f"{anio}-{mes_fin:02d}-{calendar.monthrange(anio, mes_fin)[1]:02d}",
                v.upper())
    if m := re.fullmatch(r"(\d{4})-S([12])", v, re.I):   # un semestre
        anio, s = int(m.group(1)), int(m.group(2))
        mes_ini, mes_fin = ("01", "06") if s == 1 else ("07", "12")
        return (f"{anio}-{mes_ini}-01",
                f"{anio}-{mes_fin}-{calendar.monthrange(anio, int(mes_fin))[1]:02d}",
                v.upper())
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):            # un día exacto
        return v, v, v
    sys.exit(f"--periodo: no entiendo {v!r}. Formatos: YYYY · YYYY-MM · "
             "YYYY-Qn · YYYY-Sn · YYYY-MM-DD. Rango = dos valores "
             "(p.ej. 2026-Q1 2026-08).")


def resolver_periodo(valores):
    """Uno o dos valores → (desde, hasta, etiqueta). Rango inclusivo: del
    inicio del primero al final del último; vale mezclar granularidades.
    Si viene al revés, se normaliza (cotas min/max)."""
    if len(valores) > 2:
        sys.exit(f"--periodo: máximo dos valores (es un rango), llegaron "
                 f"{len(valores)}.")
    a = _granulo(valores[0])
    if len(valores) == 1:
        return a
    b = _granulo(valores[1])
    return min(a[0], b[0]), max(a[1], b[1]), f"{valores[0]} → {valores[1]}"


def cmd_buscar(args):
    periodo = resolver_periodo(args.periodo) if args.periodo else None
    aviso = (f"Buscando {args.query!r} — si este término no se ha pedido antes, "
             "computa fresco (de 20s a varios minutos según el estado del "
             "servidor); si ya está en caché, será instantáneo.")
    if args.modo:
        aviso += f"\nmodo: {args.modo} (default: híbrido, no se envía mode)."
    if periodo:
        aviso += (f"\nperíodo: {periodo[0]} → {periodo[1]} · el server recibe "
                  "la base ancha (100) — la MISMA respuesta que la búsqueda "
                  "normal; --limit es solo tu cupo de muestra. El hit no "
                  "trae fecha: se filtra tras hidratar; los hits fuera del "
                  "período se descartan.")
    print(aviso, file=sys.stderr)
    params = {"q": args.query, "limit": max(args.limit, LIMIT_BASE)}
    if args.modo:
        params["mode"] = args.modo
    if args.tags:
        tags = [t.strip().lower() for t in args.tags.split(",") if t.strip()]
        params["strict_filter"] = json.dumps({"required_tags": tags})
    data = get("/search", params=params, vacio_si_tag_inexistente=True)
    if data is None:
        print("0 resultados (el tag requerido no existe).")
        return
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    hits = data.get("results", [])
    if not hits:
        print("0 resultados.")
        return
    conv_sin_caja = 0
    ids_sin_caja = []
    mostrados = 0
    omitidas = 0
    for hit in hits:
        # Cortocircuito: al llenar el cupo se deja de hidratar.
        if mostrados >= args.limit:
            break
        nid = hit.get("note_id") or hit.get("id")
        detalle = get(f"/notes/{nid}", tolerante=True)
        if detalle is None:
            # Nota rota (status < 400, body sin JSON): no tira la búsqueda.
            omitidas += 1
            print(f"    aviso: {nid} responde sin JSON — omitida "
                  "(respuesta rota del server para esa nota)")
            continue
        nota = detalle.get("note", {})
        if periodo:
            # Comparación por DÍA (los 10 primeros chars de la fecha ISO):
            # el período es inclusivo en ambos extremos, en UTC.
            fecha = (nota.get("created_at_utc") or "")[:10]
            if not (periodo[0] <= fecha <= periodo[1]):
                continue
        mostrados += 1
        print(f"[{mostrados}] {hit.get('title') or '(sin título)'}")
        print(f"    id: {nid} · fecha: {nota.get('created_at_utc', '—')} · "
              f"score: {hit.get('score', '—')}")
        tags = ", ".join(detalle.get("tags", [])) or "—"
        print(f"    tags: {tags}")
        abstract = ((nota.get("metadata") or {}).get("ai_extracted") or {}).get("abstract")
        if not abstract:  # por si el abstract viniera a nivel de hit
            abstract = hit.get("abstract")
        if abstract:
            print(f"    abstract: {abstract}")
        if es_conversacion(detalle, hit):
            caja = tiene_caja(detalle)
            print(f"    caja: {'presente' if caja else 'AUSENTE'}")
            if not caja:
                conv_sin_caja += 1
                ids_sin_caja.append(nid)
        print()
    if periodo:
        if mostrados == 0:
            print("0 resultados dentro del período.")
        corto = ""
        if mostrados < args.limit:
            corto = (" · cupo no lleno: poco material de este término en el "
                     "rango, o cae fuera de la base del ranking "
                     f"(los {len(hits)} traídos)")
        omit = f" · {omitidas} omitidas por respuesta rota" if omitidas else ""
        print(f"— período {periodo[2]}: {mostrados} dentro de "
              f"{len(hits)} traídos · cupo {args.limit}{corto}{omit}.")
    elif omitidas:
        print(f"— {omitidas} notas omitidas por respuesta rota del server "
              "(nid en los avisos de arriba).")
    if conv_sin_caja:
        print(f"— Se detectaron {conv_sin_caja} conversaciones sin caja. Generando…")
        pedir_cajas(ids_sin_caja)
        print("Re-corre la query para ver las fichas actualizadas "
              "(caché: instantáneo).")


def cmd_nota(args):
    d = get(f"/notes/{args.nota}")
    if args.json:
        print(json.dumps(d, indent=2, ensure_ascii=False))
        return
    nota = d.get("note", {})
    original = (d.get("original") or {}).get("content") or ""
    revised = (d.get("revised") or {}).get("content") or ""
    print(f"ID:       {args.nota}")
    print(f"Título:   {nota.get('title') or '(sin título)'}")
    print(f"Fecha:    {nota.get('created_at_utc', '—')}")
    print(f"Tags:     {', '.join(d.get('tags', [])) or '—'}")
    concepts = d.get("concepts", [])
    if concepts:
        nombres = [c.get("pref_label") or c.get("notation") or "?" for c in concepts[:8]]
        print(f"Concepts: {', '.join(map(str, nombres))}"
              f"{' (+…)' if len(concepts) > 8 else ''}")
    print(f"Original: {len(original):,} chars · Revised: {len(revised):,} chars")
    abstract = ((nota.get("metadata") or {}).get("ai_extracted") or {}).get("abstract")
    print(f"Abstract: {abstract or '(sin abstract)'}")
    print(f"Caja:     {'presente' if MARCA_CAJA in revised else 'ausente'}")


def cmd_caja(args):
    d = get(f"/notes/{args.caja}")
    revised = (d.get("revised") or {}).get("content") or ""
    if MARCA_CAJA in revised:
        print(revised)
        return
    print("Sin caja — pidiéndola al black boxer y esperando…")
    if pedir_cajas([args.caja]) == 1:
        d = get(f"/notes/{args.caja}")   # re-hidratar: el revised cambió
        revised = (d.get("revised") or {}).get("content") or ""
        if MARCA_CAJA in revised:
            print(revised)
            return
    print("La caja no se generó (daemon caído o error del modelo — "
          "mira docker logs fortemi-black-boxer-1).")


def cmd_original(args):
    d = get(f"/notes/{args.original}")
    print((d.get("original") or {}).get("content") or "")


def main():
    # Cortar con | head sale limpio: comportamiento Unix estándar,
    # el proceso muere en silencio al romperse el pipe.
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    p = argparse.ArgumentParser(
        description="Querier de Fortemi — niveles: abstract → caja → original")
    p.add_argument("query", nargs="?", help="texto a buscar")
    p.add_argument("--nota", help="ficha de una nota por id")
    p.add_argument("--caja", help="la caja (revised) si existe; si no, se genera")
    p.add_argument("--original", help="la conversación cruda")
    p.add_argument("--tags", help="flat tags separados por coma (chatgpt, gemini…)")
    p.add_argument("--limit", type=int, default=20,
                   help="cupo de fichas MOSTRADAS (default 20). El server "
                        "recibe siempre una base ancha (100, o tu cupo si "
                        "pides más) para que normal y --periodo compartan "
                        "la misma respuesta cacheada.")
    p.add_argument("--periodo", nargs="+", metavar="PERIODO",
                   help="filtro de fecha, tras la query: YYYY · YYYY-MM · "
                        "YYYY-Qn · YYYY-Sn · YYYY-MM-DD; rango = dos valores "
                        "(2026-Q1 2026-08). Filtra del lado del cliente sobre "
                        "la fecha hidratada; no aplica a --json ni a los "
                        "modos por nota.")
    p.add_argument("--modo", choices=["fts", "semantic", "hybrid"], default=None,
                   help="modo de búsqueda (default: híbrido — no se envía "
                        "mode al server). fts = solo léxico, sin semántica ni "
                        "vía del embed; red manual para los días que la "
                        "búsqueda fresca cuelga.")
    p.add_argument("--offset", type=int, default=0,
                   help="salto de resultados — IGNORADO en silencio por la "
                        "build actual de /search (v2026.7.1, verificado "
                        "30-sep-2026): no pagina")
    p.add_argument("--json", action="store_true", help="salida cruda (debug)")
    a = p.parse_args()
    modos = sum(bool(x) for x in (a.nota, a.caja, a.original))
    if modos > 1:
        p.error("--nota/--caja/--original son excluyentes")
    if a.periodo and modos:
        p.error("--periodo aplica solo a la búsqueda (no a --nota/--caja/--original)")
    if a.modo and modos:
        p.error("--modo aplica solo a la búsqueda (no a --nota/--caja/--original)")
    if a.periodo and a.json:
        p.error("--periodo filtra tras hidratar; --json es salida cruda — no combinan")
    if a.nota:
        cmd_nota(a)
    elif a.caja:
        cmd_caja(a)
    elif a.original:
        cmd_original(a)
    elif a.query:
        cmd_buscar(a)
    else:
        p.error("falta la query o un modo (--nota/--caja/--original)")


if __name__ == "__main__":
    main()
