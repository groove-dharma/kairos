#!/usr/bin/env python3
"""Prepara el input del debate a partir de una corrida de GTFlow.

Determinista, sin LLM.

EL PROBLEMA QUE RESUELVE
------------------------
GTFlow usa DOS vocabularios distintos y no lo dice en ninguna parte:

  - evidence_catalog.json  -> codigos CRUDOS del open coding
                              ('desire_for_self_erasure', 'task_bajar', 'exhaustion')
  - codebook.json          -> categorias CONSOLIDADAS, renombradas por el LLM
                              ('emotional_state_ideation_suicidal', 'task_pending')

Si al debate le das las categorias del codebook y un indice construido sobre los
codigos crudos, los agentes preguntan por 'emotional_state_despair', reciben
"ningun codigo coincide", y queman todas sus tool calls adivinando nombres.

El puente es el campo `include` (y `aliases`) de cada CodebookEntry: contiene los
codigos crudos que esa categoria consolida. Este script lo usa para indexar cada
categoria bajo su propio nombre Y bajo los crudos.

TAMBIEN VERIFICA LAS CITAS
--------------------------
GTFlow no comprueba que la frase in-vivo exista en el texto del seg_id que
reclama; su etapa de calidad lo mide despues de construir la teoria. En una corrida temprana solo
una fraccion pequena coincidia: el modelo asigna codigos a un seg_id vecino. La atribucion a
nivel de NOTA aguanta ese desplazamiento; la frase no. Aqui la frase se conserva
solo si aparece literalmente, y si no se usa el texto del propio segmento.

Salidas:
    debate_input.json   categorias (con n_notas real) + triples + limites
    code_index.json     nombre -> [{note_id, frase, literal}], por categoria y por crudo

Uso:
    python prep_debate_input.py --run-dir /app/corpus/run_<periodo> --period <periodo>
"""

import argparse
import json
import os
import re
from collections import defaultdict


def _cargar_parser():
    """El cliente de Fortemi del parser: OAuth, paginacion y las rarezas de la
    API ya resueltas. Se importa en vez de duplicarlo."""
    import importlib.util
    from pathlib import Path as _P
    ruta = _P(__file__).resolve().parent / "e2_parser.py"
    spec = importlib.util.spec_from_file_location("ep_prep", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seccion(cuerpo, nombre):
    m = re.search(rf"^##\s*{re.escape(nombre)}[^\n]*\n(.*?)(?=\n##\s|\n---\s*\n|\Z)",
                  cuerpo, re.S | re.M)
    return m.group(1).strip() if m else ""


def _por_fecha(periodos):
    """Ordena etiquetas de periodo cronologicamente, mezclando granularidades."""
    def clave(p):
        r = _un_periodo(p)
        return (r[0] if r else "9999-99-99", str(p))
    return sorted({str(x) for x in periodos if str(x).strip()}, key=clave)


def leer_de_fortemi():
    """Las hipotesis vivas, leidas de FORTEMI -- no de un theory.json.

    POR QUE. Antes la herencia venia del theory.json del periodo anterior, que
    trae los IDs LOCALES de aquel debate. Esos IDs no coinciden con los tags de
    Fortemi: un id local podia no ser el tag de Fortemi. Los agentes citaban el
    numero local, y en la nota publicada esa referencia apuntaba a otra
    hipotesis distinta -- o a ninguna.

    Leyendo de Fortemi, la hipotesis llega con SU TAG REAL. El agente ve H-025,
    en Fortemi hay h-025, y son la misma. No hay dos numeraciones, asi que no
    hay nada que traducir despues.

    La nota tiene todo lo que traia el JSON: patron, mecanismo, prediccion,
    falsador, estado, evidencia e historia. Se parsea del markdown que escribio
    el parser.
    """
    ep = _cargar_parser()
    H_RE = re.compile(r"[Hh]-(\d{3,})")
    UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    fuera = []
    for n in ep.notas_por_tag("thesis", 5000):
        nota = ep.get_note(n.get("id"))
        if not nota:
            continue
        cuerpo = nota.get("content") or ""
        tags = [t.get("name", t) if isinstance(t, dict) else t
                for t in (nota.get("tags") or [])]
        tag_h, estado, periodos = "", "", []
        for t in tags:
            m = H_RE.fullmatch(str(t).strip())
            if m:
                tag_h = f"H-{int(m.group(1)):03d}"
            elif str(t).lower().startswith("estado/"):
                estado = str(t).split("/", 1)[1]
            elif str(t).lower().startswith("periodo/"):
                periodos.append(str(t).split("/", 1)[1])
        if not tag_h:
            continue
        # Las falsadas no se heredan, igual que antes.
        if estado == "falsado":
            continue
        titulo = re.search(r"^#\s*[Hh]-\d+\s*[—-]\s*(.+)$", cuerpo, re.M)
        cita = re.search(r"^>\s*(.+)$", cuerpo, re.M)
        evid = list(dict.fromkeys(UUID_RE.findall(_seccion(cuerpo, "Evidencia"))))
        origen = re.search(r"\*\*Periodo de origen:\*\*\s*([0-9A-Za-z\-]+)", cuerpo)
        fuera.append({
            "id": tag_h,                     # el TAG de Fortemi, no un id local
            "pattern": _seccion(cuerpo, "Patron"),
            "mechanism": _seccion(cuerpo, "Mecanismo"),
            "prediction": _seccion(cuerpo, "Prediccion"),
            "falsifier": _seccion(cuerpo, "Falsador"),
            "asunto": (titulo.group(1).strip() if titulo else "")[:80],
            "resumen": (cita.group(1).strip() if cita else "")[:400],
            # Ordenados por FECHA, no alfabeticamente. sorted() pone "2026-05"
            # antes que "2026-q1", pero enero-marzo va antes que mayo: el origen
            # saldria mal en cuanto se mezclen formatos.
            "origin_period": (origen.group(1) if origen
                              else (_por_fecha(periodos)[0] if periodos else "")),
            "periods_tested": _por_fecha(periodos),
            "status": estado or "activo",
            "evidence": evid,
            "evidence_by_period": {},
            "n_evidence": len(evid),
            "_note_id": n.get("id"),
        })
    fuera.sort(key=lambda x: x["id"])
    return fuera

SEG_ID_RE = re.compile(r"^(?P<note_id>.+)-(?P<idx>\d{1,4})$")
PERIODO_ANUAL = re.compile(r"^(\d{4})$")
PERIODO_TRIM = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)

TRIM_RANGOS = {
    1: ("01-01", "03-31"), 2: ("04-01", "06-30"),
    3: ("07-01", "09-30"), 4: ("10-01", "12-31"),
}


def norm(s: str) -> str:
    """Normaliza un nombre de codigo para comparar entre vocabularios."""
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").lower()).strip("_")


def tokens(s: str) -> set:
    return {t for t in norm(s).split("_") if len(t) > 2}


def note_id_from_seg(seg_id: str) -> str | None:
    m = SEG_ID_RE.match(str(seg_id or "").strip())
    return m.group("note_id") if m else None


_DIAS_MES = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _ultimo_dia(anio: int, mes: int) -> int:
    if mes == 2 and not (anio % 4 == 0 and (anio % 100 or anio % 400 == 0)):
        return 28
    return _DIAS_MES[mes - 1]


def _un_periodo(p: str) -> tuple[str, str] | None:
    """Los limites de UN periodo suelto, en YYYY-MM-DD."""
    p = str(p).strip().upper()
    m = re.fullmatch(r"(\d{4})", p)
    if m:
        a = m.group(1)
        return f"{a}-01-01", f"{a}-12-31"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", p)
    if m:
        a, mes = int(m.group(1)), int(m.group(2))
        if 1 <= mes <= 12:
            return f"{a}-{mes:02d}-01", f"{a}-{mes:02d}-{_ultimo_dia(a, mes):02d}"
        return None
    m = re.fullmatch(r"(\d{4})-Q([1-4])", p)
    if m:
        a, q = int(m.group(1)), int(m.group(2))
        ini, fin = (q - 1) * 3 + 1, (q - 1) * 3 + 3
        return f"{a}-{ini:02d}-01", f"{a}-{fin:02d}-{_ultimo_dia(a, fin):02d}"
    m = re.fullmatch(r"(\d{4})-S([12])", p)
    if m:
        a, s = int(m.group(1)), int(m.group(2))
        return ((f"{a}-01-01", f"{a}-06-30") if s == 1
                else (f"{a}-07-01", f"{a}-12-31"))
    return None


def limites_periodo(periodo: str) -> tuple[str | None, str | None]:
    """Los limites de fecha del periodo. Entiende los mismos formatos que
    muestras.py, incluidos los rangos que este produce.

    ESTO NO ES DECORATIVO: sin limites, el aislamiento temporal se apaga y los
    agentes pueden citar como evidencia notas de cualquier periodo. Antes solo
    se reconocian año y trimestre, asi que una muestra de un mes o de un rango
    (2026-05_a_2026-06) se quedaba sin filtro y no avisaba.

    Formatos: 2026 | 2026-05 | 2026-Q2 | 2026-S1 | 2026-05_a_2026-06
    """
    p = str(periodo).strip()

    # Rango, tal como lo nombra muestras.py: "<desde>_a_<hasta>"
    if "_a_" in p:
        izq, der = p.split("_a_", 1)
        a, b = _un_periodo(izq), _un_periodo(der)
        if a and b:
            return f"{min(a[0], b[0])}T00:00:00Z", f"{max(a[1], b[1])}T23:59:59Z"
        return None, None

    r = _un_periodo(p)
    if r:
        return f"{r[0]}T00:00:00Z", f"{r[1]}T23:59:59Z"
    return None, None


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def primera_oracion(texto: str, max_chars: int) -> str:
    t = " ".join(str(texto or "").split())
    if len(t) <= max_chars:
        return t
    corte = t[:max_chars]
    punto = corte.rfind(". ")
    return (corte[:punto + 1] if punto > max_chars // 2 else corte).strip()


def indice_crudo(catalogo: dict, max_frase: int) -> tuple[dict, dict]:
    """codigo crudo -> [{note_id, frase, literal}] + estadisticas de verificacion."""
    indice = defaultdict(list)
    st = {"verificadas": 0, "descartadas": 0, "sin_frase": 0, "segmentos": 0}

    for seg_id, rec in catalogo.items():
        nid = note_id_from_seg(seg_id)
        if not nid:
            continue
        st["segmentos"] += 1
        texto = str(rec.get("text") or "")
        texto_cf = texto.casefold()

        frase, literal = "", False
        candidatas = [str(c).strip() for c in (rec.get("in_vivo_phrases") or []) if str(c).strip()]
        for c in candidatas:
            if c.casefold() in texto_cf:
                frase, literal = c[:max_frase], True
                st["verificadas"] += 1
                break
            st["descartadas"] += 1
        if not literal:
            if not candidatas:
                st["sin_frase"] += 1
            frase = " ".join(texto.split())[:max_frase]

        for code in rec.get("codes") or []:
            indice[str(code)].append({"note_id": nid, "frase": frase, "literal": literal})

    return dict(indice), st


def dedup(entradas: list) -> list:
    """Una entrada por nota, prefiriendo la cita literal."""
    mejor = {}
    for e in entradas:
        prev = mejor.get(e["note_id"])
        if prev is None or (e["literal"] and not prev["literal"]):
            mejor[e["note_id"]] = e
    return sorted(mejor.values(), key=lambda x: (not x["literal"], x["note_id"]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--period", required=True, help="'2022' o '2024-Q3'")
    ap.add_argument("--out", default=None)
    ap.add_argument("--index-out", default=None)
    ap.add_argument("--max-triples", type=int, default=60)
    ap.add_argument("--max-def-chars", type=int, default=160)
    ap.add_argument("--max-notes-per-triple", type=int, default=8)
    ap.add_argument("--max-frase-chars", type=int, default=180)
    ap.add_argument("--sin-herencia", action="store_true",
                    help="No heredar nada de Fortemi (util para depurar)")
    ap.add_argument("--previous", nargs="*", default=[],
                    help="theory.json de corridas anteriores. Sus hipotesis vivas se "
                         "pasan al debate para que se refuercen o se falsen aqui.")
    args = ap.parse_args()

    run = args.run_dir
    out_path = args.out or os.path.join(run, "debate_input.json")
    idx_path = args.index_out or os.path.join(run, "code_index.json")

    codebook = read_json(os.path.join(run, "codebook.json"))
    triples = read_json(os.path.join(run, "axial_triples.json"))
    if not isinstance(triples, list):
        triples = triples.get("triples") or []

    # ── Indice sobre codigos crudos ──────────────────────────────────
    crudo, st = {}, {}
    n_segmentos = n_notas = 0
    cat_path = os.path.join(run, "evidence_catalog.json")
    if os.path.exists(cat_path):
        catalogo = read_json(cat_path)
        n_segmentos = len(catalogo)
        n_notas = len({note_id_from_seg(s) for s in catalogo} - {None})
        crudo, st = indice_crudo(catalogo, args.max_frase_chars)

    crudo_por_norm = defaultdict(list)
    for c in crudo:
        crudo_por_norm[norm(c)].append(c)

    # ── Puente categoria -> codigos crudos ───────────────────────────
    indice, categorias = {}, []
    via = {"exacto": 0, "include": 0, "tokens": 0, "sin_evidencia": 0}
    huerfanos = []

    for e in codebook.get("entries") or []:
        if not isinstance(e, dict):
            continue
        cat = str(e.get("code") or "").strip()
        if not cat:
            continue

        fuentes, ruta = [], ""
        # 1. El nombre de la categoria existe tal cual como codigo crudo
        for c in crudo_por_norm.get(norm(cat), []):
            fuentes.append(c)
        if fuentes:
            ruta = "exacto"

        # 2. include + aliases: el puente que GTFlow deja para esto
        if not fuentes:
            for etiqueta in (e.get("include") or []) + (e.get("aliases") or []):
                for c in crudo_por_norm.get(norm(etiqueta), []):
                    if c not in fuentes:
                        fuentes.append(c)
            if fuentes:
                ruta = "include"

        # 3. Ultimo recurso: solapamiento de tokens del nombre
        if not fuentes:
            t_cat = tokens(cat)
            if t_cat:
                for c in crudo:
                    t_c = tokens(c)
                    if t_c and len(t_cat & t_c) >= max(1, min(len(t_cat), len(t_c)) - 1):
                        fuentes.append(c)
            if fuentes:
                ruta = "tokens"

        entradas = dedup([x for c in fuentes for x in crudo.get(c, [])])
        if entradas:
            indice[cat] = entradas
            via[ruta] += 1
        else:
            via["sin_evidencia"] += 1
            huerfanos.append(cat)

        categorias.append({
            "code": cat,
            "definition": primera_oracion(e.get("definition"), args.max_def_chars),
            "n_notas": len(entradas),
        })

    # Los codigos crudos tambien quedan consultables, sin pisar categorias
    for c, entradas in crudo.items():
        if c not in indice:
            indice[c] = dedup(entradas)

    # ── Triples ──────────────────────────────────────────────────────
    triples_out, sin_patron, sin_condition = [], set(), 0
    for t in triples:
        if not isinstance(t, dict):
            continue
        notas = []
        for s in (t.get("evidence") or []):
            nid = note_id_from_seg(s)
            if nid:
                if nid not in notas:
                    notas.append(nid)
            else:
                sin_patron.add(str(s))
        if not notas:
            continue
        if not t.get("condition"):
            sin_condition += 1
        triples_out.append({
            "condition": t.get("condition"),
            "action": t.get("action"),
            "result": t.get("result"),
            "n_evidence": len(notas),
            "note_ids": notas[:args.max_notes_per_triple],
        })
    triples_out.sort(key=lambda x: x["n_evidence"], reverse=True)
    total_con_notas = len(triples_out)
    triples_out = triples_out[:args.max_triples]

    semilla = sorted({n for t in triples_out for n in t["note_ids"]})
    ini, fin = limites_periodo(args.period)

    # ── Hipotesis vivas de periodos anteriores ───────────────────────
    # Una hipotesis no es un snapshot: si su prediction se cumple aqui, se refuerza;
    # si el material de este periodo la contradice, se falsa. Crecen, no se duplican.
    previas, vistas = [], set()
    if args.sin_herencia:
        pass
    else:
        try:
            for h in leer_de_fortemi():
                if h["id"] in vistas:
                    continue
                vistas.add(h["id"])
                previas.append(h)
        except Exception as e:
            print(f"AVISO: no se pudo leer la herencia de Fortemi ({e}).")
            print("       Se sigue sin herencia. Comprueba que Fortemi este arriba.")

    # ── Archivo central de pendientes ────────────────────────────────
    # Las hipotesis en NUEVO no se escriben en Fortemi: nadie las examino. Pero
    # tampoco se pierden. Viven aqui, en un solo sitio, y entran a todas las
    # corridas siguientes hasta que alguien las debata. El parser las saca de
    # aqui en cuanto dejan de estar en NUEVO.
    pend_path = os.path.join(os.path.dirname(os.path.dirname(args.run_dir)), "pendientes.json")
    if not os.path.exists(pend_path):
        pend_path = "/app/corpus/pendientes.json"
    # SE RENUMERAN al cargarlas, continuando desde el mayor de Fortemi.
    #
    # Una pendiente nunca se escribio, asi que su id es el LOCAL del debate que
    # la propuso -- y esos van muy por encima de los tags reales: con 49 notas
    # en Fortemi, las pendientes traian H-064 y H-065, y el debate arrancaba a
    # numerar en H-066. El agente veia numeros que en Fortemi no existen.
    #
    # Aqui reciben el numero que les tocaria si se escribieran ahora. El id
    # viejo no se guarda: no apunta a ninguna nota, no sirve para nada y
    # confundiria a quien lo viera.
    n_pend = 0
    _mayor = 0
    for _h in previas:
        _m = re.fullmatch(r"[Hh]-(\d+)", str(_h.get("id", "")).strip())
        if _m:
            _mayor = max(_mayor, int(_m.group(1)))
    if os.path.exists(pend_path):
        try:
            for h in (read_json(pend_path) or {}).get("pendientes", []):
                _mayor += 1
                h["id"] = f"H-{_mayor:03d}"
                if h["id"] in vistas:
                    continue
                vistas.add(h["id"])
                h["_pendiente"] = True
                previas.append(h)
                n_pend += 1
        except Exception as e:
            print(f"aviso: no se pudo leer {pend_path}: {e}")

    payload = {
        "period": args.period,
        "period_start": ini,
        "period_end": fin,
        "source_run": os.path.abspath(run),
        "code_index": os.path.abspath(idx_path),
        "n_notas": n_notas,
        "n_segmentos": n_segmentos,
        "categorias": categorias,
        "triples_axiales": triples_out,
        "note_ids_semilla": semilla,
        "hipotesis_previas": previas,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    cats_indexadas = [c["code"] for c in categorias if c["n_notas"] > 0]
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump({"period": args.period, "verificacion": st, "resolucion": via,
                   "categorias_indexadas": cats_indexadas, "index": indice},
                  f, ensure_ascii=False, indent=2)

    # ── Informe ──────────────────────────────────────────────────────
    tot_frases = st.get("verificadas", 0) + st.get("descartadas", 0)
    pct = (st.get("verificadas", 0) / tot_frases * 100) if tot_frases else 0.0
    notas_idx = len({e["note_id"] for v in indice.values() for e in v})
    con_ev = len(categorias) - via["sin_evidencia"]

    print(f"periodo              : {args.period}")
    print(f"limites              : {ini} .. {fin}" if ini else
          "limites              : NO RECONOCIDOS")
    print(f"notas / segmentos    : {n_notas} / {n_segmentos}")
    print()
    print(f"categorias codebook  : {len(categorias)}")
    print(f"  con evidencia      : {con_ev}")
    print(f"    por nombre exacto: {via['exacto']}")
    print(f"    via include      : {via['include']}")
    print(f"    via tokens       : {via['tokens']}")
    print(f"  sin evidencia      : {via['sin_evidencia']}"
          + (f"  {huerfanos[:4]}" if huerfanos else ""))
    print()
    print(f"codigos crudos       : {len(crudo)}")
    print(f"claves del indice    : {len(indice)}   ({notas_idx} notas alcanzables)")
    print(f"  citas literales    : {st.get('verificadas', 0):,} de {tot_frases:,} ({pct:.0f}%)")
    print(f"  citas descartadas  : {st.get('descartadas', 0):,}")
    print()
    print(f"triples con notas    : {total_con_notas} de {len(triples)}"
          + (f" (recortado a {len(triples_out)})" if total_con_notas > len(triples_out) else ""))
    print(f"  sin condition      : {sin_condition}")
    print(f"note_ids semilla     : {len(semilla)}")
    print(f"seg_id sin patron    : {len(sin_patron)}")
    if previas:
        n_fortemi = len(previas) - n_pend
        print(f"hipotesis previas    : {n_fortemi} vivas leidas de FORTEMI"
              + (f" (+{n_pend} pendientes del archivo central)" if n_pend else ""))
        print(f"  llegan con su TAG de Fortemi, no con un id local")
        for h in previas[:5]:
            print(f"  {h['id']} [{h['status']}] {h['pattern'][:70]}...")
    print()
    print(f"escrito -> {out_path}  ({os.path.getsize(out_path) / 1024:.1f} KB)")
    print(f"escrito -> {idx_path}  ({os.path.getsize(idx_path) / 1024:.1f} KB)")

    if not ini:
        print("\nAVISO: periodo no reconocido. El debate no filtrara note_ids por fecha.")
    if not indice:
        print("\nAVISO: sin evidence_catalog. notas_por_codigo no funcionara.")
    elif con_ev < len(categorias) * 0.5:
        print(f"\nAVISO: solo {con_ev} de {len(categorias)} categorias tienen evidencia. "
              "El codebook consolido nombres que no se pueden mapear a los codigos crudos. "
              "Los agentes veran n_notas=0 en el resto y deben ignorarlas.")
    if tot_frases and pct < 40:
        print(f"\nAVISO: solo el {pct:.0f}% de las citas del open coding son literales. "
              "Es el desplazamiento de seg_id conocido de GTFlow. La atribucion por nota "
              "aguanta; las frases no literales van marcadas [segmento] y no se citan.")
    if total_con_notas < 15:
        print(f"\nNOTA: solo {total_con_notas} relaciones axiales, pero el debate alcanza "
              f"{notas_idx} notas por el indice, no solo las {len(semilla)} de las triples.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())