#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_concatenacion.py — como se funden dos hipotesis en una.

LA REGLA, tal como se decidio: FUSIONAR ES CONCATENAR, NO RESUMIR.

Cuando H-002 se funde en H-001, el campo `Patron` de la nota resultante contiene
el patron de H-001 y debajo el de H-002. No se parafrasea, no se reescribe, no
se pide a ningun modelo que "unifique" el texto. Cada aportacion se queda tal
cual la escribio quien la escribio.

Cada aportacion va precedida de su TIMESTAMP DE ADHERENCIA -- cuando ese texto
entro en esta nota -- haciendo de viñeta:

    ## Patron
    `2000-01-01 00:00` Texto de la primera aportacion.

    `2000-01-02 00:00` Texto de la segunda aportacion.

Asi se ve de un vistazo que la hipotesis tiene dos aportaciones y cuando llego
cada una, sin que nadie tenga que explicarlo en prosa.

Las notas que YA existen no tienen ese formato: su campo es texto plano. Se
tratan como una sola aportacion, y se les pone como timestamp la fecha de
creacion de la nota.
"""

from __future__ import annotations
import re
from datetime import datetime, timezone

CAMPOS = ("Patron", "Mecanismo", "Prediccion", "Falsador")
RE_TS = re.compile(r"^`([^`]+)`\s+(.*)$", re.S)


def ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def seccion(cuerpo: str, nombre: str) -> str:
    """El texto crudo de una seccion `## Nombre`, sin la cabecera."""
    m = re.search(rf"^##\s*{re.escape(nombre)}\s*\n(.*?)(?=\n##\s|\n---\s*\n|\Z)",
                  cuerpo, re.S | re.M)
    return m.group(1).strip() if m else ""


def aportaciones(texto: str, ts_por_defecto: str) -> list[tuple[str, str]]:
    """Parte una seccion en sus aportaciones (timestamp, texto).

    Si no lleva timestamps -- las notas escritas antes de esta regla -- es una
    sola aportacion y se le pone el timestamp que se pase.
    """
    texto = (texto or "").strip()
    if not texto:
        return []
    trozos = [t.strip() for t in re.split(r"\n\s*\n", texto) if t.strip()]
    fuera = []
    for t in trozos:
        m = RE_TS.match(t)
        if m:
            fuera.append((m.group(1).strip(), m.group(2).strip()))
        else:
            fuera.append((ts_por_defecto, t))
    return fuera


def render(aports: list[tuple[str, str]]) -> str:
    """De vuelta a texto, con el timestamp de viñeta."""
    return "\n\n".join(f"`{ts}` {txt}" for ts, txt in aports if txt.strip())


def fundir_campo(texto_canon: str, ts_canon: str,
                 texto_nuevo: str, ts_nuevo: str) -> str:
    """Concatena sin repetir: si la aportacion ya esta, no se duplica."""
    aports = aportaciones(texto_canon, ts_canon)
    vistos = {re.sub(r"\s+", " ", t).strip().lower() for _, t in aports}
    for ts, txt in aportaciones(texto_nuevo, ts_nuevo):
        clave = re.sub(r"\s+", " ", txt).strip().lower()
        if clave and clave not in vistos:
            aports.append((ts, txt))
            vistos.add(clave)
    return render(aports)


def reemplazar_seccion(cuerpo: str, nombre: str, contenido: str) -> str:
    """Sustituye el contenido de `## Nombre`. Si no existe, la anade al final."""
    patron = re.compile(rf"(^##\s*{re.escape(nombre)}\s*\n)(.*?)(?=\n##\s|\n---\s*\n|\Z)",
                        re.S | re.M)
    if patron.search(cuerpo):
        return patron.sub(lambda m: m.group(1) + contenido + "\n", cuerpo, count=1)
    return cuerpo.rstrip() + f"\n\n## {nombre}\n{contenido}\n"


def fundir_notas(cuerpo_canon: str, cuerpo_otra: str,
                 ts_canon: str, ts_otra: str) -> str:
    """El cuerpo de la nota canonica despues de absorber a la otra.

    Los cuatro campos se concatenan. Lo demas de la canonica se queda como esta:
    su titulo, su estado, su resumen, su evidencia y su historia -- eso lo
    actualiza quien llama, que es el que sabe los ids.
    """
    fuera = cuerpo_canon
    for campo in CAMPOS:
        a = seccion(cuerpo_canon, campo)
        b = seccion(cuerpo_otra, campo)
        if not b:
            continue
        fuera = reemplazar_seccion(fuera, campo, fundir_campo(a, ts_canon, b, ts_otra))
    return fuera


def anadir_aportacion(cuerpo: str, campo: str, texto: str,
                      ts: str = "", ts_por_defecto: str = "") -> str:
    """Anade UNA aportacion a un campo. Lo usa el parser cuando el debate saca
    otro angulo de una hipotesis que ya existe."""
    ts = ts or ahora()
    actual = seccion(cuerpo, campo)
    return reemplazar_seccion(
        cuerpo, campo,
        fundir_campo(actual, ts_por_defecto or ts, texto, ts))


def seccion_flex(cuerpo: str, nombre: str) -> str:
    """Igual que seccion(), pero tolera parentesis en el titulo de la seccion.

    cc.seccion() usa un patron que no matchea:
        ## Evidencia (5 notas)
    porque los parentesis estan antes del newline. seccion_flex usa
    [^\n]* en su lugar, que casa cualquier caracter hasta el newline.
    """
    m = re.search(
        r"^##\s*" + re.escape(nombre) + r"[^\n]*\n(.*?)(?=\n##\s|\n---\s*\n|\Z)",
        cuerpo, re.S | re.M)
    return m.group(1).strip() if m else ""
