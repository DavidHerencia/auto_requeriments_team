import argparse
from pathlib import Path
from config import cargar_config
from workflow import ejecutar_desde_aris


def main():
    parser = argparse.ArgumentParser(description="RDA XML: comenzar directamente en ARIS")
    parser.add_argument("--xml", type=Path, required=True, help="Ruta local del XML de prueba")
    args = parser.parse_args()
    resultado = ejecutar_desde_aris(args.xml, cargar_config())
    raise SystemExit(0 if resultado["estado"] in ("inspeccionado", "importado") else 1)

if __name__ == "__main__":
    main()
