import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def cargar_config():
    ruta = ROOT / "config.local.json"
    if not ruta.exists():
        raise RuntimeError("Copiar config.example.json a config.local.json y completar aris_url")
    c = json.loads(ruta.read_text(encoding="utf-8"))
    if not c.get("aris_url", "").startswith("https://"):
        raise ValueError("aris_url debe ser una URL HTTPS configurada localmente")
    if c.get("modo_aris") not in ("inspeccionar", "importar"):
        raise ValueError("modo_aris debe ser inspeccionar o importar")
    return c
