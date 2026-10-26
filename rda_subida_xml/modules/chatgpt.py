"""Procesamiento por registro: captura GPT configurable, XML y checkpoint."""
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr
from playwright.sync_api import sync_playwright
from modules.download_xmls import validar
from config import CHROME_PROFILE

LOG = logging.getLogger(__name__)
INICIAL = ('Corrige el xml usando y siguiendo las instrucciones de "BPMN ARIS XML Repair". '
           'Dame solamente el xml listo para descargar sin nada mas o en caso de no poder '
           'solo ponmelo para copiar y pegar.')
RECUPERAR = 'Dame solamente el xml listo para descargar sin nada mas o en caso de no poder solo ponmelo para copiar y pegar.'


def nombre_seguro(id_n5, nombre_proceso):
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', f'{id_n5} {nombre_proceso}')
    base = re.sub(r'\s+', ' ', base).strip(' .')
    if not base:
        raise ValueError('Nombre vacio')
    base = base[:145].rstrip(' .')
    if base.split('.')[0].upper() in {'CON','PRN','AUX','NUL'} | {f'{p}{i}' for p in ('COM','LPT') for i in range(1,10)}:
        base = '_' + base
    return base + ' - Detalle'


def extraer_xml(texto):
    marca = chr(96) * 3
    bloques = re.findall(re.escape(marca) + r'(?:xml)?\s*\n(.*?)' + re.escape(marca), texto, flags=re.I | re.S)
    validos = []
    for candidato in (bloques if bloques else [texto]):
        candidato = candidato.strip().lstrip('\ufeff')
        try:
            ET.fromstring(candidato)
            validos.append(candidato)
        except ET.ParseError:
            pass
    if len(validos) != 1:
        raise ValueError('No se encontro un XML valido unico')
    return validos[0]


def corregir_lane(texto, id_n5, nombre_proceso):
    ET.fromstring(texto)
    tags = re.finditer(r'<[A-Za-z_][\w.:-]*\b[^>]*>', texto, flags=re.S)
    candidatos = [m for m in tags if re.search(r'\bid\s*=\s*(["\x27])LanePrincipal\1', m.group())]
    if len(candidatos) != 1:
        raise ValueError('LanePrincipal debe existir exactamente una vez')
    m = candidatos[0]
    atributos = list(re.finditer(r'\bname\s*=\s*(["\x27]).*?\1', m.group(), flags=re.S))
    if len(atributos) != 1:
        raise ValueError('LanePrincipal no tiene un unico name')
    a = atributos[0]
    name = f'{id_n5} {nombre_proceso}'
    tag = m.group()[:a.start()] + 'name=' + quoteattr(name) + m.group()[a.end():]
    final = texto[:m.start()] + tag + texto[m.end():]
    raiz = ET.fromstring(final)
    encontrados = [x for x in raiz.iter() if x.attrib.get('id') == 'LanePrincipal']
    if len(encontrados) != 1 or encontrados[0].attrib.get('name') != name:
        raise ValueError('Fallo verificando atributo LanePrincipal')
    return final


def checkpoint(ruta, registros):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix('.tmp')
    if temporal.exists():
        raise FileExistsError(temporal)
    temporal.write_text(json.dumps({'version': 1, 'registros': registros}, indent=2, ensure_ascii=False), encoding='utf-8')
    temporal.replace(ruta)


def inspeccionar(config, perfil):
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(perfil), channel='chrome', headless=False, accept_downloads=True)
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(config.get('hero_gpt_url') or config.get('chatgpt_url') or 'https://chatgpt.com/', wait_until='domcontentloaded')
            print('Abre HERO - BPMN Modeling. Revisa skill BPMN ARIS XML Repair.')
            print('Identifica URL del GPT, input archivo, editor, enviar, turno completo y descarga.')
            input('Enter para cerrar; no se envia nada en inspeccion...')
        finally:
            browser.close()


def interactuar(xml_path, config, perfil, temporal):
    campos = ('gpt_archivo_selector','gpt_editor_selector','gpt_enviar_selector',
              'gpt_respuesta_selector','gpt_final_selector')
    if any(not config.get(k) for k in campos):
        raise ValueError('Primero inspeccionar y configurar selectores verificados')
    url = config.get('hero_gpt_url', '')
    if not url.startswith('https://chatgpt.com/g/'):
        raise ValueError('Configura hero_gpt_url con enlace directo al GPT HERO')
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(str(perfil), channel='chrome', headless=False, accept_downloads=True)
        try:
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.goto(url, wait_until='domcontentloaded')
            input('Verifica GPT HERO y sesion; Enter para preparar...')
            if input('Escribe ENVIAR para autorizar este XML: ') != 'ENVIAR':
                return None
            sel = config['gpt_respuesta_selector']
            timeout = int(config.get('timeout_ms', 120000))
            def enviar(prompt, archivo=None):
                anterior = page.locator(sel).count()
                if archivo:
                    page.locator(config['gpt_archivo_selector']).set_input_files(str(archivo))
                page.locator(config['gpt_editor_selector']).fill(prompt)
                page.locator(config['gpt_enviar_selector']).click()
                page.wait_for_function(
                    '([s,n]) => document.querySelectorAll(s).length > n',
                    arg=[sel, anterior], timeout=timeout)
                nodo = page.locator(sel).nth(anterior)
                nodo.locator(config['gpt_final_selector']).wait_for(state='visible', timeout=timeout)
                return nodo
            def recuperar(nodo):
                descarga_sel = config.get('gpt_descarga_selector')
                if descarga_sel and nodo.locator(descarga_sel).count():
                    with page.expect_download(timeout=timeout) as info:
                        nodo.locator(descarga_sel).first.click()
                    descarga = info.value
                    if not descarga.suggested_filename.lower().endswith('.xml'):
                        raise ValueError('La descarga no es un archivo XML')
                    descarga.save_as(temporal)
                    return extraer_xml(temporal.read_text(encoding='utf-8-sig'))
                return extraer_xml(nodo.inner_text())
            primero = enviar(INICIAL, xml_path)
            try:
                return recuperar(primero)
            except ValueError:
                LOG.warning('Primera respuesta terminada sin XML valido. Un reintento de contenido.')
            segundo = enviar(RECUPERAR)
            return recuperar(segundo)
        finally:
            browser.close()


def procesar_registros(origen, config, base):
    datos = json.loads(origen.read_text(encoding='utf-8'))
    registros = validar(datos['registros'])
    perfil = CHROME_PROFILE
    if config.get('modo_chatgpt','inspeccionar') == 'inspeccionar':
        inspeccionar(config, perfil)
        return None
    if config.get('modo_chatgpt') != 'ejecutar':
        raise ValueError('modo_chatgpt invalido')
    ruta = base/'exportaciones'/('procesados_'+origen.stem+'.json')
    if ruta.exists():
        guardados = json.loads(ruta.read_text(encoding='utf-8'))['registros']
        if [x['id_solicitud'] for x in guardados] != [x['id_solicitud'] for x in registros]:
            raise ValueError('Checkpoint de otra exportacion')
        trabajos = guardados
    else:
        trabajos = [dict(x) for x in registros]
        checkpoint(ruta, trabajos)
    for i, r in enumerate(trabajos, 1):
        if r.get('estado') in ('correccion_finalizada','correccion_fallida','requiere_revision','envio_iniciado'):
            LOG.info('Saltando registro %s estado=%s', i, r['estado'])
            continue
        nombre = nombre_seguro(r['id_n5'], r['nombre_proceso'])
        original = base/'originales'/(nombre+'.xml')
        final = base/'corregidos'/(nombre+'.xml')
        original.parent.mkdir(parents=True, exist_ok=True)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            r.update(estado='requiere_revision', motivo='Ya existe el archivo final')
            checkpoint(ruta, trabajos)
            continue
        if not original.exists():
            original.write_text(r['xml'], encoding='utf-8')
        temporal = base/'temporales'/(nombre+'.xml')
        temporal.parent.mkdir(parents=True, exist_ok=True)
        try:
            LOG.info('Procesando %s/%s solicitud=%s', i, len(trabajos), r['id_solicitud'])
            r['estado'] = 'envio_iniciado'
            checkpoint(ruta, trabajos)
            texto = interactuar(original, config, perfil, temporal)
            if texto is None:
                r.update(estado='requiere_revision', motivo='Cancelado antes de enviar')
            else:
                contenido = corregir_lane(texto, r['id_n5'], r['nombre_proceso'])
                with final.open('x', encoding='utf-8') as f:
                    f.write(contenido)
                r.update(estado='correccion_finalizada', id_archivo_n5=nombre,
                         xml_corregido_ruta=str(final))
                r.pop('motivo', None)
        except ValueError as ex:
            r.update(estado='correccion_fallida', motivo=str(ex)[:160])
            LOG.warning('Registro %s sin XML final valido', i)
        except Exception as ex:
            r.update(estado='requiere_revision', motivo=type(ex).__name__)
            LOG.exception('Registro %s requiere revision; no reenvio automatico', i)
        finally:
            temporal.unlink(missing_ok=True)
            checkpoint(ruta, trabajos)
    LOG.info('Resultados: %s', ruta)
    return ruta
