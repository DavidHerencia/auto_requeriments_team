"""Interacción ChatGPT: inspección primero, sin adivinar selectores."""
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from playwright.sync_api import sync_playwright
from modules.download_xmls import validar

PROMPT = '@skill_revisor_y_corregidor_xml'

def nombre_seguro(id_n5, nombre_proceso):
    nombre = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', f'{id_n5} {nombre_proceso}')
    nombre = re.sub(r'\s+', ' ', nombre).strip(' .')
    if not nombre:
        raise ValueError('Nombre vacío')
    if nombre.split('.')[0].upper() in {'CON','PRN','AUX','NUL'} | {f'{p}{n}' for p in ('COM','LPT') for n in range(1,10)}:
        nombre = '_' + nombre
    return nombre[:160].rstrip(' .')

def xml_de_respuesta(texto):
    bloques = re.findall(r'```(?:xml)?\s*\n(.*?)```', texto, flags=re.S|re.I)
    candidatos = bloques if bloques else [texto.strip()]
    validos = []
    for c in candidatos:
        try:
            ET.fromstring(c.strip())
            validos.append(c.strip())
        except ET.ParseError:
            pass
    if len(validos) != 1:
        raise ValueError('ChatGPT no entregó un único XML bien formado')
    return validos[0]

def obtener_respuesta(xml_path, cfg, perfil):
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        nav = p.chromium.launch_persistent_context(str(perfil),channel='chrome',headless=False)
        try:
            page = nav.pages[0] if nav.pages else nav.new_page()
            page.goto(cfg.get('chatgpt_url','https://chatgpt.com/'),wait_until='domcontentloaded')
            if cfg.get('modo_chatgpt','inspeccionar') == 'inspeccionar':
                input('Inspecciona interfaz y skill; Enter cierra SIN enviar...')
                return None
            claves = ['gpt_archivo_selector','gpt_editor_selector','gpt_enviar_selector',
                      'gpt_respuesta_selector','gpt_final_selector']
            if any(not cfg.get(k) for k in claves):
                raise ValueError('Primero identificar selectores reales de ChatGPT')
            input('Abre el chat de la skill; Enter para continuar...')
            anteriores = page.locator(cfg['gpt_respuesta_selector']).count()
            page.locator(cfg['gpt_archivo_selector']).set_input_files(str(xml_path))
            page.locator(cfg['gpt_editor_selector']).fill(PROMPT)
            if input('Para ENVIAR el XML a ChatGPT escribe ENVIAR: ') != 'ENVIAR':
                return None
            page.locator(cfg['gpt_enviar_selector']).click()
            page.wait_for_function(
                '([sel,n]) => document.querySelectorAll(sel).length > n',
                arg=[cfg['gpt_respuesta_selector'],anteriores],
                timeout=int(cfg.get('timeout_ms',120000)))
            page.locator(cfg['gpt_final_selector']).wait_for(state='visible',timeout=int(cfg.get('timeout_ms',120000)))
            return page.locator(cfg['gpt_respuesta_selector']).nth(anteriores).inner_text()
        finally:
            nav.close()

def corregir_primero(origen: Path, cfg: dict, base: Path):
    paquete = json.loads(origen.read_text(encoding='utf-8'))
    registros = validar(paquete['registros'])
    if len(registros) != 1:
        raise ValueError('Actualmente corrección solo de UN registro; lote se habilitará después')
    r = dict(registros[0])
    nombre = nombre_seguro(r['id_n5'],r['nombre_proceso'])
    original = base/'originales'/(nombre+'.xml')
    corregido = base/'corregidos'/(nombre+'.xml')
    original.parent.mkdir(parents=True,exist_ok=True)
    corregido.parent.mkdir(parents=True,exist_ok=True)
    if original.exists() or corregido.exists():
        raise FileExistsError('Archivo ya existente; no se sobrescribe')
    # El XML original podría ser inválido: la skill debe poder corregirlo.
    original.write_text(r['xml'],encoding='utf-8')
    respuesta = obtener_respuesta(original,cfg,base/'chrome_profile_gpt')
    if respuesta is None:
        print('Inspección terminada; ningún XML corregido guardado')
        return None
    corregido.write_text(xml_de_respuesta(respuesta),encoding='utf-8')
    r.update(id_archivo_n5=nombre,estado='correccion_finalizada',
             xml_corregido_ruta=str(corregido))
    destino = base/'exportaciones'/('corregido_'+origen.stem+'.json')
    if destino.exists():
        raise FileExistsError('Ya existe JSON corregido')
    destino.write_text(json.dumps({'version':1,'registros':[r]},ensure_ascii=False,indent=2),encoding='utf-8')
    return destino
