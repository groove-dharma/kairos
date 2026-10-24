#!/usr/bin/env python3
"""
Limpia entradas de state.json (y rotaciones) basándose en archivos en no-procesados/.
Uso: docker exec fortemi-daemon-1 python3 /app/clean_state.py
"""

import json
import os
from pathlib import Path

STATE_DIR = Path("/protesis/estado")
STATE_FILE = STATE_DIR / "state.json"
NO_PROCESADOS_DIR = Path("/dictados/no-procesados")

def main():
    if not NO_PROCESADOS_DIR.exists():
        print(f"Error: {NO_PROCESADOS_DIR} no existe")
        return

    no_procesados = {f.name for f in NO_PROCESADOS_DIR.iterdir() if f.is_file()}
    print(f"Archivos en no-procesados: {len(no_procesados)}")

    if not no_procesados:
        print("No hay archivos en no-procesados")
        return

    # --- state.json ---
    removed_main = 0
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        to_remove = [name for name in state if name in no_procesados]
        for name in to_remove:
            del state[name]
        removed_main = len(to_remove)

        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

        print(f"\nstate.json: {removed_main} entradas removidas")
        for name in to_remove:
            print(f"  - {name}")
    else:
        print(f"state.json no encontrado en {STATE_FILE}")

    # --- Rotaciones ---
    archive_files = sorted(STATE_DIR.glob("state-*.json"))
    removed_archive = 0

    for archive_file in archive_files:
        with open(archive_file, "r", encoding="utf-8") as f:
            archive = json.load(f)

        to_remove = [name for name in archive if name in no_procesados]
        for name in to_remove:
            del archive[name]

        if to_remove:
            with open(archive_file, "w", encoding="utf-8") as f:
                json.dump(archive, f, indent=2, ensure_ascii=False)
            removed_archive += len(to_remove)
            print(f"\n{archive_file.name}: {len(to_remove)} entradas removidas")
            for name in to_remove:
                print(f"  - {name}")

    print(f"\nTotal: {removed_main + removed_archive} entradas removidas de {1 + len(archive_files)} archivos")

if __name__ == "__main__":
    main()
