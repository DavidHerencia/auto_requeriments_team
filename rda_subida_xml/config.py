import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data'
CHROME_PROFILE = DATA / 'chrome_profile_sheets'  # Perfil persistente comun del RDA
def cargar_config():
    ruta=ROOT/'config.local.json'
    if not ruta.exists():
        raise RuntimeError('Copia config.example.json a config.local.json')
    return json.loads(ruta.read_text(encoding='utf-8'))
