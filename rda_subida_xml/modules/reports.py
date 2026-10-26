import json
from pathlib import Path
from datetime import datetime, timezone


def guardar_reporte(data: dict, carpeta: Path):
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = datetime.now(timezone.utc).strftime('ejecucion_%Y%m%dT%H%M%S_%fZ.json')
    ruta = carpeta / nombre
    ruta.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    return ruta
