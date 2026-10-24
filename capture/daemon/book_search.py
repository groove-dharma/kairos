"""
Catálogo de libros: escaneo de directorios y matching difuso de títulos.
Tolerante a mayúsculas, acentos, nombres parciales y errores de transcripción.
"""

import re
import unicodedata
import difflib
from pathlib import Path

EXTENSIONES_LIBRO = {".pdf", ".epub", ".txt", ".md", ".html", ".htm"}


# ─── Catálogo de libros ───

def escanear_libros(libros_dir):
    """Devuelve la lista de nombres de archivos en el directorio de libros."""
    if not libros_dir.exists():
        return []
    return [f.stem for f in libros_dir.iterdir()
            if f.suffix.lower() in EXTENSIONES_LIBRO]


def encontrar_libro(libro_referenciado, libros_dir):
    """Busca un libro por nombre. Búsqueda difusa tolerante a mayúsculas, acentos y nombres parciales."""
    if not libros_dir.exists():
        return None

    def normalizar(s):
        s = unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode('utf-8')
        return s.lower().strip()

    obj_norm = normalizar(libro_referenciado)

    for arch in libros_dir.iterdir():
        if arch.suffix.lower() in EXTENSIONES_LIBRO and normalizar(arch.stem) == obj_norm:
            return arch

    for arch in libros_dir.iterdir():
        if arch.suffix.lower() in EXTENSIONES_LIBRO:
            if obj_norm in normalizar(arch.stem) or normalizar(arch.stem) in obj_norm:
                return arch

    palabras_obj = set(re.sub(r'[^\w\s]', '', obj_norm).split())
    mejor_arch = None
    max_puntuacion = 0

    for arch in libros_dir.iterdir():
        if arch.suffix.lower() not in EXTENSIONES_LIBRO:
            continue
        nombre_arch_norm = normalizar(arch.stem)
        palabras_arch = set(re.sub(r'[^\w\s]', '', nombre_arch_norm).split())
        puntuacion = 0
        for p_obj in palabras_obj:
            if len(p_obj) <= 2:
                if p_obj in palabras_arch:
                    puntuacion += 0.1
                continue
            if p_obj in palabras_arch:
                puntuacion += 1
            else:
                for p_arch in palabras_arch:
                    if difflib.SequenceMatcher(None, p_obj, p_arch).ratio() >= 0.8:
                        puntuacion += 0.8
                        break
        puntuacion_final = puntuacion - (len(palabras_arch) * 0.01)
        if puntuacion_final > max_puntuacion and puntuacion > 0:
            max_puntuacion = puntuacion_final
            mejor_arch = arch

    return mejor_arch
