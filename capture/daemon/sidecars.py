"""
sidecars.py — Lectura y escritura de sidecars de KOReader (metadata.*.lua)

Parsea la tabla Lua serializada por dump.lua (KOReader), permite modificar
solo lo que toca y re-serializa preservando todo lo demás (kopt_*, stats,
doc_props, bookmarks, pboxes...). Parser propio, stdlib pura, sin dependencias.

Dialecto de dump.lua (verificado contra specimen real, KOReader 2025.08):
  - "return {" con claves ["string"] y [N], indentación de 4
  - escapes estándar (\\n \\t \\\" \\\\) + el caso especial de %q de LuaJIT:
    backslash seguido de salto de línea real = newline en el string
  - floats con los dígitos exactos del original; enteros sin punto

CLI:
  python3 sidecars.py --check RUTA_AL_METADATA.LUA
    Round-trip: parse → dump → parse → compara. La prueba de la fase 1.
"""

import re
import sys
import shutil
import argparse
from pathlib import Path
from datetime import datetime, timezone


SIDECAR_GLOB = "metadata.*.lua"
# .old = backup de KOReader · .sync-conflict = formato moderno de syncthing
# "~" = formato antiguo de conflicto (metadata.pdf~YYYYMMDD-HHMMSS.lua)
# .daemon-bak = backups del propio daemon
EXCLUDE = (".old", ".sync-conflict", ".daemon-bak", "~")


class LuaParseError(Exception):
    pass


# ─── Parser ───

class _Parser:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def error(self, msg):
        line = self.text.count("\n", 0, self.pos) + 1
        raise LuaParseError(f"{msg} (línea {line})")

    def skip_ws(self):
        while self.pos < self.n:
            c = self.text[self.pos]
            if c in " \t\r\n,":
                self.pos += 1
            elif c == "-" and self.pos + 1 < self.n and self.text[self.pos + 1] == "-":
                nl = self.text.find("\n", self.pos)
                self.pos = self.n if nl == -1 else nl + 1
            else:
                return

    def peek(self):
        return self.text[self.pos] if self.pos < self.n else ""

    def expect(self, s):
        if not self.text.startswith(s, self.pos):
            self.error(f"se esperaba {s!r}")
        self.pos += len(s)

    def parse_value(self):
        self.skip_ws()
        c = self.peek()
        if c == "{":
            return self.parse_table()
        if c == '"':
            return self.parse_string('"')
        if c == "'":
            return self.parse_string("'")
        if self.text.startswith("true", self.pos):
            self.pos += 4
            return True
        if self.text.startswith("false", self.pos):
            self.pos += 5
            return False
        if self.text.startswith("nil", self.pos):
            self.pos += 3
            return None
        return self.parse_number()

    def parse_table(self):
        self.expect("{")
        out = {}
        while True:
            self.skip_ws()
            c = self.peek()
            if c == "}":
                self.pos += 1
                return out
            if c == "":
                self.error("tabla sin cerrar")
            if c != "[":
                self.error("se esperaba clave '[' (dialecto dump.lua)")
            self.pos += 1
            self.skip_ws()
            key = self.parse_key()
            self.skip_ws()
            self.expect("=")
            out[key] = self.parse_value()

    def parse_key(self):
        c = self.peek()
        if c == '"':
            key = self.parse_string('"')
            self.skip_ws()
            self.expect("]")
            return key
        m = re.match(r"(-?\d+)\s*\]", self.text[self.pos:])
        if not m:
            self.error("clave no reconocida")
        self.pos += m.end()
        return int(m.group(1))

    def parse_string(self, q):
        self.expect(q)
        buf = []
        while True:
            if self.pos >= self.n:
                self.error("string sin cerrar")
            c = self.text[self.pos]
            if c == q:
                self.pos += 1
                return "".join(buf)
            if c == "\\":
                self.pos += 1
                e = self.text[self.pos] if self.pos < self.n else ""
                if e == "\n":              # %q de LuaJIT: `\` + LF = newline
                    buf.append("\n")
                    self.pos += 1
                elif e == "\r":            # variante CRLF
                    buf.append("\n")
                    self.pos += 1
                    if self.peek() == "\n":
                        self.pos += 1
                elif e == "n": buf.append("\n"); self.pos += 1
                elif e == "t": buf.append("\t"); self.pos += 1
                elif e == "r": buf.append("\r"); self.pos += 1
                elif e == "a": buf.append("\a"); self.pos += 1
                elif e == "b": buf.append("\b"); self.pos += 1
                elif e == "f": buf.append("\f"); self.pos += 1
                elif e == "v": buf.append("\v"); self.pos += 1
                elif e == "\\": buf.append("\\"); self.pos += 1
                elif e == q: buf.append(q); self.pos += 1
                elif e == "'": buf.append("'"); self.pos += 1
                elif e == "x":
                    m = re.match(r"x([0-9a-fA-F]{1,2})", self.text[self.pos:])
                    if not m:
                        self.error("escape \\x inválido")
                    buf.append(chr(int(m.group(1), 16)))
                    self.pos += m.end()
                elif e.isdigit():
                    m = re.match(r"\d{1,3}", self.text[self.pos:])
                    buf.append(chr(int(m.group())))
                    self.pos += m.end()
                else:
                    self.error(f"escape no reconocido: \\{e}")
            else:
                buf.append(c)
                self.pos += 1

    def parse_number(self):
        m = re.match(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", self.text[self.pos:])
        if not m:
            self.error("valor no reconocido")
        lit = m.group()
        self.pos += len(lit)
        if "." in lit or "e" in lit or "E" in lit:
            return float(lit)
        return int(lit)


def parse(text):
    """Parsea un sidecar completo → (data, header). El header es el comentario
    inicial de dump.lua (path original del documento); se conserva tal cual."""
    header = ""
    body = text
    stripped = text.lstrip()
    if stripped.startswith("--"):
        nl = stripped.find("\n")
        header = stripped[:nl] if nl != -1 else stripped
        body = stripped[nl + 1:] if nl != -1 else ""
    p = _Parser(body)
    p.skip_ws()
    p.expect("return")
    data = p.parse_value()
    return data, header


# ─── Serializer ───

def _escape(s):
    out = []
    for ch in s:
        if ch == "\\": out.append("\\\\")
        elif ch == '"': out.append('\\"')
        elif ch == "\n": out.append("\\n")
        elif ch == "\r": out.append("\\r")
        elif ch == "\t": out.append("\\t")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03d" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def _fmt_key(k):
    if isinstance(k, int):
        return f"[{k}]"
    return f'["{_escape(k)}"]'


def _fmt_number(v):
    if isinstance(v, int):
        return str(v)
    return repr(v)  # float → representación más corta que round-trippea


def _dump_table(d, lines, ind, step):
    for k, v in d.items():
        if v is None:
            continue  # dump.lua omite nil
        prefix = " " * ind + _fmt_key(k) + " = "
        if isinstance(v, bool):
            lines.append(prefix + ("true" if v else "false") + ",")
        elif isinstance(v, dict):
            if v:
                lines.append(prefix + "{")
                _dump_table(v, lines, ind + step, step)
                lines.append(" " * ind + "},")
            else:
                lines.append(prefix + "{},")
        elif isinstance(v, str):
            lines.append(prefix + '"' + _escape(v) + '",')
        else:
            lines.append(prefix + _fmt_number(v) + ",")


def dump(data, header=None, indent=4):
    lines = []
    if header:
        lines.append(header.rstrip("\n"))
    lines.append("return {")
    _dump_table(data, lines, indent, indent)
    lines.append("}")
    return "\n".join(lines) + "\n"


# ─── API de alto nivel ───

def leer_sidecar(path):
    text = Path(path).read_text(encoding="utf-8")
    return parse(text)


def escribir_sidecar(path, data, header, backup_dir):
    """Backup previo + escritura atómica. Devuelve la ruta del backup."""
    path = Path(path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = backup_dir / f"{path.parent.name}__{path.name}__{ts}.bak"
    shutil.copy2(path, bak)
    out = dump(data, header)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(path)
    return bak


def anotaciones_de(data):
    """(clave_viva, dict_de_anotaciones). La base 'annotations' es la primaria
    según KOReader; 'annotations_paging' solo se toca si es la única que existe."""
    for key in ("annotations", "annotations_paging"):
        anns = data.get(key)
        if isinstance(anns, dict):
            return key, anns
    return None, {}


def es_sidecar(path):
    name = Path(path).name
    return (name.startswith("metadata.") and name.endswith(".lua")
            and not any(x in name for x in EXCLUDE))


def buscar_sidecars(root):
    root = Path(root)
    if not root.exists():
        return []
    return sorted(p for p in root.rglob(SIDECAR_GLOB) if es_sidecar(p))


# ─── Fechas del sidecar (datetime sin zona → zona del dispositivo) ───

def parse_dt_local(s, tz):
    if not s or not isinstance(s, str):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def dt_local_a_utc_iso(s, tz):
    dt = parse_dt_local(s, tz)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── CLI: --check (round-trip) ───

def _diff(a, b, path="raíz"):
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        return [f"{path}: tipo {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        out = []
        for k in a:
            if k not in b:
                out.append(f"{path}.{k}: falta en re-parse")
            else:
                out.extend(_diff(a[k], b[k], f"{path}.{k}"))
        for k in b:
            if k not in a:
                out.append(f"{path}.{k}: sobra en re-parse")
        return out
    if a != b:
        return [f"{path}: {a!r} != {b!r}"]
    return []


def _cmd_check(path):
    p = Path(path)
    if not p.exists():
        print(f"NO EXISTE: {p}")
        return 1
    original = p.read_text(encoding="utf-8")
    data, header = parse(original)
    out1 = dump(data, header)
    data2, header2 = parse(out1)
    out2 = dump(data2, header2)
    _, anns = anotaciones_de(data)
    print(f"archivo      : {p.name}")
    print(f"anotaciones : {len(anns)}")
    print(f"original    : {len(original)} bytes")
    print(f"re-serializado: {len(out1)} bytes")
    print(f"dump estable (dump(dump(x)) == dump(x)): {'OK' if out1 == out2 else 'FALLA'}")
    diffs = _diff(data, data2)
    if diffs:
        print(f"ROUND-TRIP: FALLA ({len(diffs)} diferencias)")
        for d in diffs[:20]:
            print(f"  - {d}")
        return 1
    print("ROUND-TRIP: OK — parse → dump → parse es identidad")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Sidecars de KOReader: parse/dump/round-trip")
    ap.add_argument("--check", metavar="RUTA", help="valida round-trip sobre un metadata.*.lua real")
    args = ap.parse_args()
    if args.check:
        sys.exit(_cmd_check(args.check))
    ap.print_help()
