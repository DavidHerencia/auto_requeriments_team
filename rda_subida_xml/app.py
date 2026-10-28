import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from config import DATA, CHROME_PROFILE, cargar_config
from workflow import ejecutar_desde_aris, ejecutar_lote_aris
from pipeline import ejecutar_pipeline
from modules.download_xmls import descargar_xmls
from modules.chatgpt import procesar_registros

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fase', choices=['download','chatgpt','aris','pipeline'], required=True)
    parser.add_argument('--archivo', type=Path)
    parser.add_argument('--ids', nargs='+', metavar='ID', help='Pipeline: IDs exactos del Sheet (máx. --limite)')
    parser.add_argument('--reintentar-solicitud', metavar='ID', help='ARIS: tras verificar ausencia del modelo en ARIS')
    parser.add_argument('--limite', type=int, default=1,
                        help='Máximo de XML por fase, p.ej. --limite 4')
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
        if cfg.get('modo_chatgpt') != 'ejecutar':
            parser.error('Para enviar XML cambia modo_chatgpt a ejecutar en config.local.json')
        procesar_registros(args.archivo, cfg, DATA, limite=args.limite)
    elif args.fase == 'pipeline':
        ejecutar_pipeline(cfg, limite=args.limite, ids=args.ids, archivo=args.archivo)
    else:
        if not args.archivo:
            parser.error('--archivo obligatorio')
        if args.archivo.suffix.lower() == '.json':
            ejecutar_lote_aris(args.archivo, cfg, limite=args.limite,
                               reintentar_solicitud=args.reintentar_solicitud)
        elif args.archivo.suffix.lower() == '.xml':
            ejecutar_desde_aris(args.archivo, cfg)
        else:
            parser.error('--archivo debe ser un JSON de registros corregidos o XML local')

if __name__ == '__main__':
    main()
