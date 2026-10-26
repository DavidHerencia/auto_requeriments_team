from pathlib import Path
import re
import xml.etree.ElementTree as ET


def preparar_xml(origen: Path, destino: Path):
    if not origen.is_file():
        raise FileNotFoundError(origen)
    data = origen.read_bytes()
    ET.fromstring(data)
    destino.mkdir(parents=True, exist_ok=True)
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', origen.stem).strip(' .') or 'proceso'
    salida = destino / (nombre + '.xml')
    if salida.exists():
        raise FileExistsError(f"Archivo existente: {salida}; no se sobrescribe")
    salida.write_bytes(data)
    return salida
