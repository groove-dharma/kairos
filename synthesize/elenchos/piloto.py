#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
piloto.py — el chofer del N3.

Corre una muestra de punta a punta:

    muestra.jsonl
      -> GTFlow etapas 1-5   (segment, open coding, evidence, codebook, axial)
      -> prep_debate_input.py
      -> e2_debate.py
      -> e2_parser.py

POR QUE IMPORTA GTFLOW EN VEZ DE LLAMAR AL CLI
----------------------------------------------
GTFlow NO tiene comandos por etapa. Sus subcomandos reales son exactamente seis:
segment, run-all, html-report, report, compare-projects, merge-projects.
Verificado leyendo gtflow/cli.py del repo (todos los @app.command).

`run-all` solo acepta -i -c -o --force. No hay forma de decirle "parate en la
etapa 5". Correrlo entero significa pagar y ejecutar selective coding (la etapa
que fabrica personajes), negatives, gioia y quality — justo el organo que
reemplazamos.

Las funciones del pipeline si son importables. Este piloto las llama
directamente, con el mismo provider, el mismo rate limiter y la misma config
YAML que usaria el CLI. Es la unica manera de correr 1-5 y parar.

Efecto colateral que evita: GTFlow escribe su teoria fabricada en theory.json,
el MISMO nombre que usa nuestro debate en el mismo directorio. Al no correr la
etapa 6 nunca, la colision desaparece.

USO
---
    python /app/piloto.py --muestra /app/corpus/muestras/<periodo>.jsonl --periodo <periodo>
    python /app/piloto.py --todas                      # las 13 muestras en orden
    python /app/piloto.py --periodo <periodo> --desde debate    # reanudar
    python /app/piloto.py --todas --dry-run-parser     # sin escribir en Fortemi

Reanudacion: GTFlow (1-5), debate y parser miran si su artefacto ya existe y se
la saltan. --force rehace todo. --desde <etapa> tambien fuerza esa etapa en
concreto, aunque su artefacto ya exista (para eso es "reanudar desde aqui": si
lo pides explicito, es que sabes que hay que rehacerlo). Prep siempre corre —
es barato (sin LLM) y su salida puede quedar obsoleta si reparaste algo aguas
arriba, asi que no vale la pena arriesgar un debate_input.json viejo por
ahorrarse ese paso.

Por que importa esto para debate y parser especificamente: son los dos pasos
que cuestan dinero real y no son deterministas. Sin este chequeo, relanzar
`--periodo <periodo>` sin querer -- por ejemplo dentro de un `--todas` donde otro
periodo fallo y repites el lote entero -- vuelve a pagar el debate completo
aunque theory.json ya exista y este bien, y puede producir un theory.json
DISTINTO al que ya revisaste. El parser tiene el mismo riesgo pero peor: puede
crear una segunda tanda de notas H-NNN con numeracion nueva para las mismas
seis hipotesis, porque su dedup depende de una busqueda semantica con umbral,
no de saber con certeza "esto ya se escribio".
"""

from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time, traceback
from pathlib import Path

# ── Rutas dentro del container. /app = host ~/dev/fortemi/fortemi/n3/ ──────────
APP        = Path(os.environ.get("N3_APP", "/app"))
MUESTRAS   = APP / "corpus" / "muestras"
CORPUS     = APP / "corpus"
CONFIG     = APP / "gtflow_config_v2.yaml"

PREP       = APP / "prep_debate_input.py"
DEBATE     = APP / "e2_debate.py"
PARSER     = APP / "e2_parser.py"

# Orden cronologico. La herencia de hipotesis va de cada uno al siguiente.
PERIODOS = ["2022", "2023",
            "2024-Q1", "2024-Q2", "2024-Q3", "2024-Q4",
            "2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4",
            "2026-Q1", "2026-Q2", "2026-Q3"]

ETAPAS = ["gtflow", "prep", "debate", "parser"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def barra(titulo: str) -> None:
    print("\n" + "─" * 72, flush=True)
    print(f"  {titulo}", flush=True)
    print("─" * 72, flush=True)


# ══════════════════════════════════════════════════════════════════════════════
# ETAPAS 1-5 DE GTFLOW, IMPORTADAS
# ══════════════════════════════════════════════════════════════════════════════

# Periodos cuyo codebook lo construyo un modelo distinto al del config.
RESUMEN_MODELOS: list = []


def _es_contexto_excedido(e: Exception) -> bool:
    """Si el fallo fue porque la entrada no cabe en la ventana del modelo.

    El mensaje del proxy es explicito, asi que no hace falta adivinar:
      "Input length (135302) exceeds model's maximum context length (131072)"

    Se distingue de otros 400 a proposito: un 400 por otra causa no se arregla
    cambiando de modelo, y reintentar seria gastar sin motivo.
    """
    # HAY QUE RECORRER LA CADENA DE EXCEPCIONES.
    #
    # GTFlow envuelve el error original:
    #   raise RuntimeError(f"{op} failed after {n} attempt(s) ({tipo})") from err
    # asi que str(e) dice solo "Codebook request failed after 1 attempt(s)
    # (BadRequestError)". El mensaje que importa -- "Input length (135302)
    # exceeds model's maximum context length (131072)" -- esta en __cause__.
    # Mirando solo str(e) el fallback no disparaba nunca.
    textos = []
    visto = set()
    actual = e
    while actual is not None and id(actual) not in visto:
        visto.add(id(actual))
        textos.append(str(actual).lower())
        actual = actual.__cause__ or actual.__context__
    texto = " ".join(textos)
    return ("exceeds model" in texto and "context length" in texto) or \
           ("maximum context length" in texto) or \
           ("context_length_exceeded" in texto)


def gtflow_1_a_5(muestra: Path, out_dir: Path, config: Path, force: bool,
                 modelo: str = "") -> dict:
    """Segment -> open coding -> evidence -> codebook -> axial. Y para.

    Devuelve un dict con los conteos y el coste real reportado por el provider.
    """
    from gtflow.cli import _load_config, _load_segments        # noqa: E402
    from gtflow.providers.base import make_provider            # noqa: E402
    from gtflow.pipeline.open_coder import (                   # noqa: E402
        run_open_coding, run_open_coding_streaming)
    from gtflow.pipeline.codebook_builder import build_codebook
    from gtflow.pipeline.evidence import build_evidence_catalog
    # (la etapa 5 va por _axial_tolerante, mas abajo, que reusa sus piezas internas)
    from gtflow.models.schemas import Segment, Codebook, OpenCodingItem
    from gtflow.utils.file_io import write_json, read_json, ensure_dir
    from gtflow.utils.jsonl_utils import write_jsonl, iter_jsonl
    from gtflow.rate_limiter import TokenBucket
    from gtflow.cost import estimate_cost

    conf = _load_config(str(config))
    if modelo:
        # El modelo del YAML se sobreescribe aqui, ANTES de make_provider.
        # No hay cambio en caliente: el provider se construye ya con el otro.
        log(f"           modelo: {modelo} (sobreescribe el del config)")
        conf.provider.model = modelo
    conf.output.out_dir = str(out_dir)
    ensure_dir(str(out_dir))
    rl = TokenBucket(conf.run.rate_limit_rps) if conf.run.rate_limit_rps else None

    p_seg   = out_dir / "segments.json"
    p_segl  = out_dir / "segments.jsonl"
    p_open  = out_dir / "open_codes.json"
    p_openl = out_dir / "open_codes.jsonl"
    p_evid  = out_dir / "evidence_catalog.json"
    p_book  = out_dir / "codebook.json"
    p_axial = out_dir / "axial_triples.json"

    resumen: dict = {}

    # ── 1. Segment (sin LLM) ──────────────────────────────────────────────────
    # Se llama al cargador de GTFlow tal cual. Reimplementarlo daba otro conteo:
    # su _load_segments_from_structured_input trata el jsonl aparte, genera los
    # seg_id con su propia regla y desduplica. Un segmentado distinto rompe el
    # puente seg_id -> note_id del prep.
    if force or not p_seg.exists():
        log("etapa 1/5  segment")
        segs = _load_segments(str(muestra), conf.run.segmentation_strategy,
                              conf.run.max_segment_chars)
        write_json(str(p_seg), [s.model_dump() for s in segs])
        write_jsonl(str(p_segl), (s.model_dump() for s in segs))
    else:
        segs = [Segment.model_validate(x) for x in read_json(str(p_seg))]
        log(f"etapa 1/5  segment — reutilizada ({len(segs)} segmentos)")
    resumen["segmentos"] = len(segs)
    log(f"           {len(segs)} segmentos")

    provider = make_provider(conf.provider)
    provider.reset_usage_totals()

    # ── 2. Open coding (LLM, lotes) ───────────────────────────────────────────
    stream = bool(conf.run.stream_open_coding)
    ya = p_openl.exists() if stream else p_open.exists()
    if force or not ya:
        log(f"etapa 2/5  open coding — {len(segs)} segmentos, lotes de {conf.run.batch_size}")
        payload = [s.model_dump() for s in segs]
        if stream:
            total, _ = run_open_coding_streaming(
                provider, payload, str(p_openl),
                batch_size=conf.run.batch_size,
                max_prompt_chars=conf.run.max_prompt_chars,
                max_retries=conf.run.retry_max,
                timeout_sec=conf.run.timeout_sec,
                rate_limiter=rl,
                output_language=conf.provider.output_language)
            items = [OpenCodingItem.model_validate(x) for x in iter_jsonl(str(p_openl))]
            write_json(str(p_open), [i.model_dump() for i in items])
        else:
            items = run_open_coding(
                provider, payload,
                batch_size=conf.run.batch_size,
                max_prompt_chars=conf.run.max_prompt_chars,
                max_retries=conf.run.retry_max,
                timeout_sec=conf.run.timeout_sec,
                rate_limiter=rl,
                max_concurrency=conf.run.concurrent_workers,
                output_language=conf.provider.output_language)
            write_json(str(p_open), [i.model_dump() for i in items])
    else:
        items = [OpenCodingItem.model_validate(x) for x in read_json(str(p_open))]
        log(f"etapa 2/5  open coding — reutilizada ({len(items)} fichas)")
    resumen["fichas"] = len(items)

    # ── 3. Evidence (sin LLM) ─────────────────────────────────────────────────
    if force or not p_evid.exists():
        log("etapa 3/5  evidence")
        cat = build_evidence_catalog([s.model_dump() for s in segs],
                                     [i.model_dump() for i in items])
        write_json(str(p_evid), cat)
    else:
        cat = read_json(str(p_evid))
        log("etapa 3/5  evidence — reutilizada")

    # ── 4. Codebook (LLM, UNA llamada con todo dentro) ────────────────────────
    # Aqui esta el techo de contexto: ~235 tokens por segmento medidos.
    # gpt-oss-120b (131K) revienta sobre ~510 segmentos.
    if force or not p_book.exists():
        techo = len(items) * 235
        log(f"etapa 4/5  codebook — {len(items)} fichas, ~{techo:,} tokens de entrada")
        if techo > 120_000:
            log(f"           AVISO: cerca o por encima del techo de gpt-oss-120b. "
                f"Si revienta con 400, usa deepseek-v4-flash o baja el TOPE del muestreo.")
        cb = _codebook_reintentando(
            provider, items, conf, rl,
            timeout_sec=conf.run.timeout_sec,
            max_retries=conf.run.retry_max,
            output_language=conf.provider.output_language)
        write_json(str(p_book), cb.model_dump())
    else:
        cb = Codebook.model_validate(read_json(str(p_book)))
        log("etapa 4/5  codebook — reutilizado")
    resumen["categorias"] = len(cb.entries)
    log(f"           {len(cb.entries)} categorias")

    # ── 5. Axial (LLM, UNA llamada) ───────────────────────────────────────────
    if force or not p_axial.exists():
        log("etapa 5/5  axial")
        triples = _axial_tolerante(
            provider, cb, cat, conf,
            timeout_sec=conf.run.timeout_sec, rate_limiter=rl,
            max_retries=conf.run.retry_max,
            output_language=conf.provider.output_language)
        write_json(str(p_axial), [t.model_dump() for t in triples])
    else:
        triples = read_json(str(p_axial))
        log("etapa 5/5  axial — reutilizado")
    resumen["triples"] = len(triples)

    # ── Coste real ────────────────────────────────────────────────────────────
    try:
        u = provider.total_usage()
        coste = estimate_cost(u, conf.provider.price_input_per_1k,
                              conf.provider.price_output_per_1k)
        resumen["coste_usd"] = round(float(coste), 4)
        log(f"           GTFlow 1-5: ${coste:.4f}")
    except Exception:
        resumen["coste_usd"] = None

    return resumen


# ══════════════════════════════════════════════════════════════════════════════
# ETAPA 5 CON TOLERANCIA POR ELEMENTO
# ══════════════════════════════════════════════════════════════════════════════

def _codebook_reintentando(provider, items, conf, rl, timeout_sec,
                           max_retries, output_language):
    """build_codebook, pero reintentando si el modelo devuelve VACIO.

    POR QUE. gpt-oss es un modelo de razonamiento: gasta tokens pensando ANTES
    de emitir. GTFlow le manda `max_tokens` desde el YAML (openai_compatible.py:
    `if max_tokens: kwargs_payload["max_tokens"] = max_tokens`), y cuando el
    razonamiento se come ese presupuesto, la API responde 200 con
    content vacio y finish_reason="length". No hay excepcion HTTP, asi que
    call_with_retry no reintenta: GTFlow parsea "" como JSON y revienta con
    "Expecting value: line 1 column 1 (char 0)" y "Model raw:" en blanco.

    Eso mato una etapa 4 tras 16 minutos de open coding ya pagados.

    Aqui se detecta el vacio y se reintenta subiendo el techo de salida. Si el
    provider no acepta el parametro, se reintenta igual: el vacio es a menudo
    intermitente.
    """
    from gtflow.pipeline.codebook_builder import build_codebook as _bc

    techo_original = getattr(provider.conf, "max_tokens", None)
    intentos = [techo_original]
    if techo_original:
        # Subir el techo da margen al razonamiento. 0/None = sin limite.
        intentos += [techo_original * 3, None]
    else:
        intentos += [None]

    ultimo_error = None
    for i, techo in enumerate(intentos):
        if techo != techo_original:
            try:
                provider.conf.max_tokens = techo
                log(f"           reintento {i}: max_tokens="
                    f"{techo if techo else 'sin limite'}")
            except Exception:
                pass
        try:
            cb = _bc(provider, items, timeout_sec=timeout_sec, rate_limiter=rl,
                     max_retries=max_retries, output_language=output_language)
            if techo != techo_original:
                try:
                    provider.conf.max_tokens = techo_original
                except Exception:
                    pass
            return cb
        except RuntimeError as e:
            ultimo_error = e
            # Solo reintentar si fue por respuesta vacia. Un fallo de otra clase
            # (400 por exceso de entrada, timeout) no se arregla reintentando.
            # La cadena entera, por si GTFlow envuelve el error (lo hace en
            # call_with_retry). Aqui build_codebook si lanza el mensaje completo,
            # pero mirar solo str(e) ya fallo una vez con el 400 de contexto.
            _t, _visto, _a = [], set(), e
            while _a is not None and id(_a) not in _visto:
                _visto.add(id(_a)); _t.append(str(_a)); _a = _a.__cause__ or _a.__context__
            texto = " ".join(_t)
            vacio = ("Model raw (first 800 chars): \n" in texto
                     or texto.rstrip().endswith("Model raw (first 800 chars):")
                     or "Expecting value: line 1 column 1" in texto)
            if not vacio or i == len(intentos) - 1:
                try:
                    provider.conf.max_tokens = techo_original
                except Exception:
                    pass
                raise
            log(f"           el modelo devolvio vacio (gasto el presupuesto "
                f"razonando). Reintentando.")
    raise ultimo_error


def _axial_tolerante(provider, codebook, evidence_catalog, conf,
                     timeout_sec, rate_limiter, max_retries, output_language):
    """Igual que build_axial de GTFlow, pero sin morir por un elemento malo.

    POR QUE HACE FALTA. build_axial hace:

        adapter = TypeAdapter(List[AxialTriple])
        triples = adapter.validate_python(normalized)

    Valida la lista ENTERA de una vez. AxialTriple exige condition, action y
    result. Si UNO solo de los ~200 elementos que devuelve el modelo se deja el
    `result`, pydantic tumba la validacion completa y build_axial lanza
    RuntimeError: se pierde la etapa 5 entera, y con ella el tiempo de open
    coding que hay detras si se relanza con --force.

    Eso paso una vez: el elemento 156 vino sin `result`. En otra ocasion no vino ninguno
    malo y por eso no se noto.

    Aqui se usa EXACTAMENTE el mismo prompt y la misma llamada al modelo que
    GTFlow -- se importan sus funciones, no se reescriben -- y lo unico que
    cambia es que se valida elemento por elemento. Los que no validan se
    descartan y se reportan. No se inventa contenido para rellenar un campo que
    falta: eso seria fabricar datos.
    """
    from gtflow.pipeline.axial_coder import (
        build_prompt, _normalize_axial_payload, _axial_response_format)
    from gtflow.pipeline.retry_utils import call_with_retry
    from gtflow.utils.json_utils import try_parse_json
    from gtflow.models.schemas import AxialTriple

    messages = build_prompt(codebook, output_language=output_language,
                            evidence_catalog=evidence_catalog)
    # RESPUESTA VACIA: el mismo fallo que en el codebook, y por lo mismo.
    # gpt-oss razona antes de emitir; si el prompt es grande (muchas categorias en
    # un periodo denso) el razonamiento agota el presupuesto de salida y la API responde
    # 200 con content vacio. try_parse_json("") lanza "Expecting value: line 1
    # column 1 (char 0)" y se pierde la etapa entera con el open coding ya pagado.
    techo_original = getattr(provider.conf, "max_tokens", None)
    intentos = [techo_original] + ([techo_original * 3, None] if techo_original else [None])
    bruto = None
    for i, techo in enumerate(intentos):
        if techo != techo_original:
            try:
                provider.conf.max_tokens = techo
                log(f"           el modelo devolvio vacio. Reintento con max_tokens="
                    f"{techo if techo else 'sin limite'}")
            except Exception:
                pass
        raw = call_with_retry(
            provider, messages,
            response_format=(_axial_response_format()
                             if getattr(provider.conf, "structured", True) else None),
            timeout_sec=timeout_sec, rate_limiter=rate_limiter,
            max_retries=max_retries, operation_name="Axial coding request")
        if str(raw or "").strip():
            try:
                bruto = _normalize_axial_payload(try_parse_json(raw))
                break
            except Exception as e:
                if i == len(intentos) - 1:
                    try:
                        provider.conf.max_tokens = techo_original
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"axial: no se pudo parsear la respuesta: {e}\n"
                        f"Model raw (primeros 400): {str(raw)[:400]}") from e
        elif i == len(intentos) - 1:
            try:
                provider.conf.max_tokens = techo_original
            except Exception:
                pass
            raise RuntimeError(
                "axial: el modelo devolvio vacio en todos los intentos. Sube "
                "max_tokens en el YAML, o usa --llm deepseek-v4-flash.")
    try:
        provider.conf.max_tokens = techo_original
    except Exception:
        pass

    if not isinstance(bruto, list):
        raise RuntimeError(f"axial: el modelo no devolvio una lista, sino {type(bruto)}")

    triples, descartados = [], []
    for i, item in enumerate(bruto):
        try:
            triples.append(AxialTriple.model_validate(item))
        except Exception as e:
            falta = [c for c in ("condition", "action", "result")
                     if not str((item or {}).get(c) or "").strip()]
            descartados.append((i, falta or str(e)[:60]))

    if descartados:
        log(f"           {len(descartados)} de {len(bruto)} triples descartadas por "
            f"venir incompletas (GTFlow habria tumbado la etapa entera):")
        for i, motivo in descartados[:5]:
            log(f"             #{i}: falta {motivo}")
        if len(descartados) > 5:
            log(f"             ... y {len(descartados)-5} mas")
    if not triples:
        raise RuntimeError("axial: ninguna triple valida. Algo va mal en el modelo o el prompt.")

    # Mismo filtrado de evidencia que hace GTFlow despues de validar.
    if evidence_catalog is not None:
        validos = {str(k) for k in evidence_catalog}
        for t in triples:
            dados = list(dict.fromkeys(str(r) for r in t.evidence if str(r)))
            t.evidence = [r for r in dados if r in validos]
            t.invalid_evidence = [r for r in dados if r not in validos]

    log(f"           {len(triples)} triples validas")
    return triples


# ══════════════════════════════════════════════════════════════════════════════
# ETAPAS PROPIAS (subprocesos)
# ══════════════════════════════════════════════════════════════════════════════

def correr(cmd: list[str], etiqueta: str) -> int:
    log(f"$ {' '.join(cmd)}")
    t0 = time.time()
    r = subprocess.run(cmd)
    log(f"{etiqueta} termino con codigo {r.returncode} en {time.time()-t0:.0f}s")
    return r.returncode


def anterior_de(periodo: str) -> str | None:
    if periodo not in PERIODOS:
        return None
    i = PERIODOS.index(periodo)
    return PERIODOS[i - 1] if i > 0 else None


def salida_hipotesis(run_dir: Path) -> Path | None:
    """El debate escribe theory.json. GTFlow etapa 6 escribiria en el mismo
    nombre, pero aqui nunca corre: el piloto para en la 5."""
    p = run_dir / "theory.json"
    return p if p.exists() else None


def una_muestra(periodo: str, args) -> dict:
    muestra = Path(args.muestra) if args.muestra else MUESTRAS / f"{periodo}.jsonl"
    run_dir = Path(args.out) if args.out else CORPUS / f"run_{periodo.replace('-', '_')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    barra(f"PERIODO {periodo}   muestra={muestra.name}   salida={run_dir.name}")
    if not muestra.exists():
        log(f"ERROR: no existe {muestra}")
        return {"periodo": periodo, "estado": "sin_muestra"}

    desde = ETAPAS.index(args.desde)
    hasta = ETAPAS.index(args.hasta)
    estado = {"periodo": periodo, "run_dir": str(run_dir)}

    # 1-5
    if desde <= ETAPAS.index("gtflow") <= hasta:
        try:
            estado.update(gtflow_1_a_5(muestra, run_dir, Path(args.config),
                                       args.force, args.llm))
        except Exception as e:
            # ── Fallback por contexto excedido ──────────────────────────────
            #
            # La etapa 4 manda las 450 fichas al modelo en UNA llamada. Si el
            # material del periodo es mas denso de lo normal, la entrada pasa
            # del techo del modelo y el proxy devuelve 400 antes de razonar
            # nada: "Input length (135302) exceeds model's maximum context
            # length (131072)". Reintentar con el mismo modelo da lo mismo.
            #
            # Un modelo con mas ventana si lo procesa. Como el provider se
            # construye al arrancar la etapa, no hace falta cambiarlo en
            # caliente: se vuelve a llamar entero con otro modelo. Las etapas
            # 1-3 ya estan en disco, asi que no se repite el open coding.
            #
            # Queda ANOTADO en el estado del periodo: si el codebook de un
            # trimestre lo construyo otro modelo, tiene que saberse -- las
            # categorias con las que se lee el periodo salen de ahi.
            # Se reintenta aunque se haya pedido un --llm explicito: si ese
            # tampoco tiene ventana, caer al alterno sigue siendo lo util.
            # Solo se salta si el alterno es el mismo que acaba de fallar.
            if (_es_contexto_excedido(e) and args.llm_alterno
                    and args.llm_alterno != args.llm):
                traceback.print_exc()
                log("")
                log(f"la entrada supera la ventana del modelo. Reintentando la "
                    f"etapa con {args.llm_alterno}.")
                log("las etapas ya escritas en disco no se repiten.")
                try:
                    estado.update(gtflow_1_a_5(muestra, run_dir, Path(args.config),
                                               False, args.llm_alterno))
                    estado["modelo_codebook"] = args.llm_alterno
                    RESUMEN_MODELOS.append({"periodo": periodo,
                                            "modelo": args.llm_alterno,
                                            "por": "contexto excedido"})
                except Exception as e2:
                    traceback.print_exc()
                    return {**estado, "estado": "fallo_gtflow", "error": str(e2)}
            else:
                traceback.print_exc()
                return {**estado, "estado": "fallo_gtflow", "error": str(e)}

    # 6a prep
    if desde <= ETAPAS.index("prep") <= hasta:
        barra(f"{periodo} — prep")
        cmd = [sys.executable, str(PREP), "--run-dir", str(run_dir), "--period", periodo]
        prev = anterior_de(periodo)
        if prev and not args.sin_herencia:
            prev_dir = CORPUS / f"run_{prev.replace('-', '_')}"
            prev_hip = salida_hipotesis(prev_dir)
            if prev_hip:
                cmd += ["--previous", str(prev_hip)]
                log(f"herencia: {prev} -> {periodo} ({prev_hip.name})")
            else:
                log(f"herencia: {prev} no tiene hipotesis en disco, se corre sin ella")
        if correr(cmd, "prep") != 0:
            return {**estado, "estado": "fallo_prep"}

    # 6b debate
    if desde <= ETAPAS.index("debate") <= hasta:
        ya_hay = salida_hipotesis(run_dir)
        valido = False
        if ya_hay:
            try:
                d = json.loads(ya_hay.read_text(encoding="utf-8"))
                valido = isinstance((d or {}).get("hypotheses"), list) and len(d["hypotheses"]) > 0
            except Exception:
                valido = False
        # Se salta solo si el artefacto es valido, no se pidio --force, y no se
        # pidio explicitamente reanudar/rehacer desde este punto. --desde debate
        # es la forma de decir "sí, quiero rehacerlo" a pesar de lo que haya.
        if valido and not args.force and args.desde != "debate":
            log(f"{periodo} — debate: theory.json ya existe con "
                f"{len(d['hypotheses'])} hipotesis. Se salta (usa --force o "
                f"--desde debate para rehacerlo).")
        else:
            barra(f"{periodo} — debate")
            cmd = [sys.executable, str(DEBATE),
                   "--input", str(run_dir / "debate_input.json"),
                   "--out-dir", str(run_dir),
                   "--max-rounds", str(args.max_rounds),
                   "--max-usd", str(args.max_usd),
                   "--max-minutes", str(args.max_minutes)]
            if args.force:
                cmd.append("--clean")
            if correr(cmd, "debate") != 0:
                return {**estado, "estado": "fallo_debate"}

    # 7 parser
    if desde <= ETAPAS.index("parser") <= hasta:
        pres = run_dir / "parser_resumen.json"
        ya_real = False
        if pres.exists() and not args.dry_run_parser:
            try:
                r = json.loads(pres.read_text(encoding="utf-8"))
                ya_real = (r or {}).get("dry_run") is False
            except Exception:
                ya_real = False
        if ya_real and not args.force and args.desde != "parser":
            log(f"{periodo} — parser: ya corrio de verdad (no en seco). Se salta "
                f"(usa --force o --desde parser para rehacerlo). Si quieres solo "
                f"previsualizar, --dry-run-parser si corre igual.")
        else:
            barra(f"{periodo} — parser")
            cmd = [sys.executable, str(PARSER), "--run-dir", str(run_dir), "--period", periodo]
            if args.dry_run_parser:
                cmd.append("--dry-run")
            if correr(cmd, "parser") != 0:
                return {**estado, "estado": "fallo_parser"}

    hip = salida_hipotesis(run_dir)
    if hip:
        try:
            data = json.loads(hip.read_text(encoding="utf-8"))
            hs = data.get("hypotheses", data if isinstance(data, list) else [])
            estado["hipotesis"] = len(hs)
            # Mismo criterio que ESTATUS en e2_parser.py: activo/refinada/defendida
            # se escriben las tres como hypothesis. Contar solo "activo" subestima
            # el resumen en cualquier corrida donde algo termine refinada o
            # defendida en vez de activo (no paso en la primera corrida porque todo cayo en
            # "activo", pero es el mismo estado a efectos del parser).
            estado["activas"] = sum(
                1 for h in hs if h.get("status") in ("activo", "refinada", "defendida"))
        except Exception:
            pass
    estado["estado"] = "ok"
    return estado


def main() -> int:
    ap = argparse.ArgumentParser(description="Piloto del N3: GTFlow 1-5 + prep + debate + parser")
    ap.add_argument("--periodo", help="Un periodo, p.ej. 2024-Q2")
    ap.add_argument("--todas", action="store_true", help="Los 13 periodos en orden cronologico")
    ap.add_argument("--muestra", help="Ruta explicita al jsonl (por defecto corpus/muestras/<periodo>.jsonl)")
    ap.add_argument("--out", help="Directorio de salida explicito")
    ap.add_argument("--config", default=str(CONFIG))
    ap.add_argument("--desde", choices=ETAPAS, default="gtflow", help="Reanudar desde esta etapa")
    ap.add_argument("--llm", default="",
                    help="Modelo para GTFlow, sobreescribe el del config. "
                         "Ej: --llm deepseek-v4-flash")
    ap.add_argument("--llm-alterno", default="deepseek-v4-flash",
                    help="Modelo al que caer si la entrada no cabe en la ventana "
                         "del principal. Vacio para no reintentar.")
    ap.add_argument("--hasta", choices=ETAPAS, default="parser",
                    help="Parar despues de esta etapa. --hasta prep prueba el cableado sin pagar debate.")
    ap.add_argument("--force", action="store_true", help="Rehacer todo, ignorar artefactos en disco")
    ap.add_argument("--sin-herencia", action="store_true", help="No pasar --previous al prep")
    ap.add_argument("--dry-run-parser", action="store_true", help="El parser no escribe en Fortemi")
    ap.add_argument("--max-rounds", type=int, default=15)
    ap.add_argument("--max-usd", type=float, default=1.50,
                    help="OJO: el default de e2_debate.py en solitario es 0.50. "
                         "El piloto siempre pasa --max-usd explicito, asi que "
                         "toda corrida automatizada usa este techo (3x el del "
                         "debate suelto), no el suyo. Una corrida real costo $0.65.")
    ap.add_argument("--max-minutes", type=int, default=45)
    ap.add_argument("--parar-si-falla", action="store_true",
                    help="Con --todas, abortar en el primer periodo que falle")
    args = ap.parse_args()

    if not args.todas and not args.periodo:
        ap.error("hace falta --periodo o --todas")

    periodos = PERIODOS if args.todas else [args.periodo]
    resultados = []
    t0 = time.time()

    for p in periodos:
        r = una_muestra(p, args)
        resultados.append(r)
        log(f"=> {p}: {r.get('estado')}")
        if r.get("estado") != "ok" and args.parar_si_falla:
            log("abortando (--parar-si-falla)")
            break

    barra(f"RESUMEN  ({(time.time()-t0)/60:.0f} min)")
    coste = 0.0
    for r in resultados:
        c = r.get("coste_usd") or 0.0
        coste += c
        print(f"  {r['periodo']:<10} {r.get('estado','?'):<14} "
              f"seg={r.get('segmentos','-'):<6} cat={r.get('categorias','-'):<4} "
              f"hip={r.get('hipotesis','-'):<4} act={r.get('activas','-'):<4} "
              f"${c:.3f}", flush=True)
    print(f"\n  GTFlow 1-5 en total: ${coste:.2f}  (el debate reporta el suyo aparte)")

    # Que periodos salieron de otro modelo. Se dice fuerte: las categorias con
    # las que se lee un periodo salen del codebook, asi que un trimestre
    # construido por otro modelo no es del todo comparable con los demas.
    if RESUMEN_MODELOS:
        print()
        print(f"  AVISO: {len(RESUMEN_MODELOS)} periodo(s) con el codebook construido "
              f"por OTRO modelo:")
        for m in RESUMEN_MODELOS:
            print(f"    {m['periodo']}: {m['modelo']}  ({m['por']})")
        print("  Sus categorias salieron de un criterio distinto al del resto.")

    resumen_path = CORPUS / "piloto_resumen.json"
    try:
        resumen_path.write_text(json.dumps(resultados, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        log(f"resumen -> {resumen_path}")
    except Exception as e:
        log(f"no se pudo escribir el resumen: {e}")

    return 0 if all(r.get("estado") == "ok" for r in resultados) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\ninterrumpido", flush=True)
        sys.exit(130)
