import argparse
from datetime import datetime, timezone
from pathlib import Path
from config import DATA, cargar_config
from workflow import ejecutar_desde_aris
from modules.download_xmls import descargar_xmls
from modules.chatgpt import corregir_primero

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fase', choices=['download','chatgpt','aris'],required=True)
    parser.add_argument('--archivo',type=Path)
    args = parser.parse_args()
    cfg = cargar_config()
    if args.fase == 'download':
        nombre = datetime.now(timezone.utc).strftime('registros_%Y%m%dT%H%M%S_%fZ.json')
        descargar_xmls(cfg,DATA/'exportaciones'/nombre,DATA/'chrome_profile_sheets')
    elif args.fase == 'chatgpt':
        if not args.archivo:
            parser.error('--archivo es obligatorio')
        corregir_primero(args.archivo,cfg,DATA)
    else:
        if not args.archivo:
            parser.error('--archivo es obligatorio')
        ejecutar_desde_aris(args.archivo,cfg)
if __name__ == '__main__':
    main()
