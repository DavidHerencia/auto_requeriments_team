"""Extracción de XML desde puente Apps Script con sesión Chrome autorizada."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

CAMPOS = ('id_solicitud', 'id_n5', 'nombre_proceso', 'xml')

def validar(datos):
    if not isinstance(datos, list):
        raise ValueError('La exportación no es una lista')
    ids = set()
    for r in datos:
        if not isinstance(r, dict) or set(r) != set(CAMPOS):
            raise ValueError('Campos de registro incorrectos')
        if any(not isinstance(r[k], str) or not r[k].strip() for k in CAMPOS):
            raise ValueError('Registro con campos vacíos')
        if r['id_solicitud'] in ids:
            raise ValueError('id_solicitud duplicado')
        ids.add(r['id_solicitud'])
    return datos

def descargar_xmls(cfg: dict, ruta: Path, perfil: Path):
    url = cfg.get('apps_script_url', '')
    if not url.startswith('https://'):
        raise ValueError('Configura apps_script_url en config.local.json')
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        navegador = p.chromium.launch_persistent_context(str(perfil), channel='chrome', headless=False)
        try:
            pagina = navegador.pages[0] if navegador.pages else navegador.new_page()
            pagina.goto(url, wait_until='domcontentloaded')
            input('Inicia sesión en Google si hace falta; al ver el puente pulsa Enter...')
            pagina.wait_for_function(
                "() => ['listo','error'].includes(document.querySelector('#estado')?.textContent)",
                timeout=int(cfg.get('timeout_ms',120000)))
            if pagina.locator('#estado').inner_text() != 'listo':
                raise RuntimeError('Apps Script: ' + pagina.locator('#error').inner_text()[:200])
            registros = validar(json.loads(pagina.locator('#resultado').inner_text()))
        finally:
            navegador.close()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if ruta.exists():
        raise FileExistsError(ruta)
    temporal = ruta.with_name(ruta.name+'.tmp')
    try:
        with temporal.open('x', encoding='utf-8') as f:
            json.dump({'version':1,'registros':registros},f,ensure_ascii=False,indent=2)
        os.replace(temporal,ruta)
    finally:
        temporal.unlink(missing_ok=True)
    print('Exportados',len(registros),'registros a',ruta)
    return ruta
