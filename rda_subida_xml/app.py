import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import DATA, CHROME_PROFILE, cargar_config
from workflow import ejecutar_desde_aris
from modules.download_xmls import descargar_xmls
from modules.chatgpt import procesar_registros

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fase', choices=['download','chatgpt','aris'], required=True)
    parser.add_argument('--archivo', type=Path)
    args = parser.parse_args()
    cfg = cargar_config()
    logs = DATA / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    fichero = logs / ('run_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%f') + '.log')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(fichero, encoding='utf-8')])
    logging.info('Inicio fase=%s; log=%s', args.fase, fichero)
    if args.fase == 'download':
        nombre = datetime.now(timezone.utc).strftime('registros_%Y%m%dT%H%M%S_%fZ.json')
        descargar_xmls(cfg, DATA/'exportaciones'/nombre, CHROME_PROFILE)
    elif args.fase == 'chatgpt':
        if not args.archivo:
            parser.error('--archivo obligatorio')
        procesar_registros(args.archivo, cfg, DATA)
    else:
        if not args.archivo:
            parser.error('--archivo obligatorio')
        ejecutar_desde_aris(args.archivo, cfg)

if __name__ == '__main__':
    main()
