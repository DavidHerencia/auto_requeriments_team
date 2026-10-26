"""Correccion XML con HERO: misma sesion durante el lote, checkpoint por registro.

La interaccion web se comprueba en la maquina del usuario; nunca se asume que
una respuesta nueva o una descarga demuestra un XML valido.
"""
import json
import logging
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import quoteattr

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from config import CHROME_PROFILE
from modules.download_xmls import validar

LOG = logging.getLogger(__name__)
INICIAL = ('Corrige el xml usando y siguiendo las instrucciones de "BPMN ARIS XML Repair". '
           'Dame solamente el xml listo para descargar sin nada mas o en caso de no poder '
           'solo ponmelo para copiar y pegar.')
RECUPERAR = ('Dame solamente el xml listo para descargar sin nada mas o en caso de no poder '
            'solo ponmelo para copiar y pegar.')
ARCHIVO = 'input[type="file"][aria-label="Adjuntar archivos"]'
EDITOR = 'div[contenteditable="true"][aria-label="Pregunta a ChatGPT"]'
ENVIAR = '[data-testid="send-button"]'
TURNO = '[data-turn-key$=":assistant"]'
RESERVADOS = {'CON', 'PRN', 'AUX', 'NUL'} | {
    f'{p}{n}' for p in ('COM', 'LPT') for n in range(1, 10)
}


class SalidaSinXml(ValueError):
    """Respuesta completa, pero sin un unico XML utilizable."""


class ResultadoIncierto(RuntimeError):
    """No se pudo verificar resultado; prohibido reintentar envio automaticamente."""


def nombre_seguro(id_n5, nombre_proceso):
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', f'{id_n5} {nombre_proceso}')
    base = re.sub(r'\s+', ' ', base).strip(' .')
    if not base:
        raise ValueError('Nombre vacio')
    base = base[:145].rstrip(' .')
    if base.split('.')[0].upper() in RESERVADOS:
        base = '_' + base
    return base + ' - Detalle'


def extraer_xml(texto):
    marca = chr(96) * 3
    bloque = re.findall(re.escape(marca) + r'(?:xml)?\s*\n(.*?)' + re.escape(marca), texto, re.I | re.S)
    candidatos = bloque if bloque else [texto]
    validos = []
    for candidato in candidatos:
        candidato = candidato.strip().lstrip('\ufeff')
        try:
            ET.fromstring(candidato)
            validos.append(candidato)
        except ET.ParseError:
            pass
    if len(validos) != 1:
        raise SalidaSinXml('No hay exactamente un XML bien formado en la respuesta')
    return validos[0]


def corregir_lane(texto, id_n5, nombre_proceso):
    raiz = ET.fromstring(texto)
    lanes = [x for x in raiz.iter() if x.attrib.get('id') == 'LanePrincipal']
    if len(lanes) != 1:
        raise SalidaSinXml('No existe un unico elemento LanePrincipal')
    # Edicion minima, sin reserializar el BPMN ni alterar prefijos XML.
    tags = re.finditer(r'<[A-Za-z_][\w.:-]*\b[^>]*>', texto, flags=re.S)
    candidatos = [m for m in tags if re.search(r'\bid\s*=\s*(["\x27])LanePrincipal\1', m.group())]
    if len(candidatos) != 1:
        raise SalidaSinXml('No se localizo exactamente el tag LanePrincipal')
    m = candidatos[0]
    attrs = list(re.finditer(r'\bname\s*=\s*(["\x27]).*?\1', m.group(), flags=re.S))
    if len(attrs) != 1:
        raise SalidaSinXml('LanePrincipal no tiene exactamente un atributo name')
    a = attrs[0]
    nombre = f'{id_n5} {nombre_proceso}'
    tag = m.group()[:a.start()] + 'name=' + quoteattr(nombre) + m.group()[a.end():]
    modificado = texto[:m.start()] + tag + texto[m.end():]
    validado = ET.fromstring(modificado)
    final = [x for x in validado.iter() if x.attrib.get('id') == 'LanePrincipal']
    if len(final) != 1 or final[0].attrib.get('name') != nombre:
        raise SalidaSinXml('No se verifico la modificacion de LanePrincipal')
    return modificado


def escribir_json(ruta, trabajos):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temp = ruta.with_name(ruta.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump({'version': 1, 'registros': trabajos}, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, ruta)
    finally:
        temp.unlink(missing_ok=True)


def gpt_url(config):
    url = config.get('hero_gpt_url', '').strip()
    partes = urlsplit(url)
    if partes.scheme != 'https' or partes.netloc != 'chatgpt.com':
        raise ValueError('Configura hero_gpt_url HTTPS del GPT HERO')
    # Si se copio una conversacion /c/..., volver a la portada del GPT.
    m = re.fullmatch(r'(/g/[^/]+)(?:/c/[^/]+)?/?', partes.path)
    if not m:
        raise ValueError('hero_gpt_url debe ser una URL /g/... de HERO')
    return 'https://chatgpt.com' + m.group(1)


def _selector(config, clave, default):
    return config.get(clave) or default


def _esperar_editor(page, config):
    sel = _selector(config, 'gpt_editor_selector', EDITOR)
    editor = page.locator(sel)
    editor.wait_for(state='visible', timeout=int(config.get('timeout_ms', 120000)))
    if editor.count() != 1:
        raise ResultadoIncierto(f'Se encontraron {editor.count()} editores (esperado: 1)')
    return editor


def _esperar_adjunto(page, xml_path, config):
    sel = _selector(config, 'gpt_archivo_selector', ARCHIVO)
    adjunto = page.locator(sel)
    if adjunto.count() != 1:
        raise ResultadoIncierto(f'Control de adjunto ambiguo (cantidad={adjunto.count()})')
    adjunto.set_input_files(str(xml_path))
    LOG.info('Adjunto seleccionado en el input de archivos')
    try:
        page.get_by_text(xml_path.name, exact=False).first.wait_for(state='visible', timeout=30000)
        LOG.info('Nombre del adjunto visible')
    except PlaywrightTimeout:
        LOG.warning('Nombre del adjunto no visible; comprobando carga observable')
    limite = time.monotonic() + 120
    sin_carga_desde = None
    while time.monotonic() < limite:
        busy = page.locator('[role="progressbar"]:visible, [aria-busy="true"]:visible').count()
        if busy == 0:
            sin_carga_desde = sin_carga_desde or time.monotonic()
            if time.monotonic() - sin_carga_desde >= 2:
                LOG.info('Adjunto sin indicadores de carga por 2 segundos')
                return
        else:
            sin_carga_desde = None
        page.wait_for_timeout(500)
    raise ResultadoIncierto('El adjunto no termino su carga visible')


def _nuevo_turno(page, config, anteriores, usuarios_antes):
    sel = _selector(config, 'gpt_respuesta_selector', TURNO)
    maximo = int(config.get('timeout_respuesta_ms', 480000))
    limite = time.monotonic() + maximo / 1000
    completado_desde = None
    ultima_longitud = -1
    generacion_vista = False
    enviado_visto = False
    siguiente_log = 0
    while time.monotonic() < limite:
        cantidad_usuario = page.locator('[data-turn-key$=":user"]').count()
        enviado_visto |= cantidad_usuario > usuarios_antes
        nodo = page.locator(sel)
        n = nodo.count()
        generando = page.get_by_role('button', name=re.compile('detener|stop|parar|cancel generation', re.I)).count() > 0
        generacion_vista |= generando
        if n > anteriores:
            if n > anteriores + 1:
                raise ResultadoIncierto('Se detectaron varios turnos nuevos de asistente')
            respuesta = nodo.nth(anteriores)
            try:
                texto = respuesta.inner_text(timeout=1000)
                largo = len(texto)
                estado = respuesta.evaluate('el => el.closest("[data-turn-state]")?.getAttribute("data-turn-state") || el.getAttribute("data-turn-state") || ""')
                finalizado = estado == 'complete'
                if largo != ultima_longitud or generando:
                    ultima_longitud = largo
                    completado_desde = None
                elif largo and not generando:
                    completado_desde = completado_desde or time.monotonic()
                # Misma estrategia ya validada en el prototipo: turno finalizado + quietud.
                quietud = 2 if finalizado else (3 if generacion_vista else 8)
                if enviado_visto and (finalizado or generacion_vista) and completado_desde and time.monotonic() - completado_desde >= quietud:
                    LOG.info('Respuesta nueva estable; caracteres=%s; turno_complete=%s', largo, finalizado)
                    if re.search(r'No se ha podido cargar esta respuesta|Something went wrong|Unable to load this response', texto, re.I):
                        raise ResultadoIncierto('ChatGPT muestra error de carga de respuesta')
                    return respuesta
            except ResultadoIncierto:
                raise
            except Exception:
                pass
        if time.monotonic() >= siguiente_log:
            LOG.info('Esperando respuesta: usuarios_nuevos=%s asistentes_nuevos=%s generando=%s',
                     enviado_visto, max(0, n - anteriores), generando)
            siguiente_log = time.monotonic() + 10
        page.wait_for_timeout(500)
    raise ResultadoIncierto('Timeout o envio sin confirmar: no se reenvia el mensaje')


def _enviar(page, config, prompt, archivo=None):
    editor = _esperar_editor(page, config)
    resp = _selector(config, 'gpt_respuesta_selector', TURNO)
    anteriores = page.locator(resp).count()
    usuarios = page.locator('[data-turn-key$=":user"]').count()
    if archivo:
        _esperar_adjunto(page, archivo, config)
    editor.fill(prompt)
    boton = page.locator(_selector(config, 'gpt_enviar_selector', ENVIAR))
    boton.wait_for(state='visible', timeout=20000)
    if boton.count() != 1:
        raise ResultadoIncierto('Boton Enviar ambiguo')
    boton.wait_for(state='visible')
    boton.click(timeout=20000)
    LOG.info('Clic Enviar realizado; adjunto=%s', bool(archivo))
    return _nuevo_turno(page, config, anteriores, usuarios)


def _leer_resultado(page, nodo, config, temporal):
    # Primero buscar archivo en el TURNO NUEVO, nunca en mensajes anteriores.
    sel = config.get('gpt_descarga_selector') or 'a[download], a[href$=".xml"]'
    candidatos = nodo.locator(sel)
    if candidatos.count() > 1:
        raise ResultadoIncierto('Multiples enlaces de descarga en la respuesta')
    if candidatos.count() == 1:
        temporal.parent.mkdir(parents=True, exist_ok=True)
        LOG.info('Posible archivo XML detectado en nueva respuesta')
        try:
            with page.expect_download(timeout=30000) as info:
                candidatos.first.click()
            descarga = info.value
            descarga.save_as(temporal)
            # No confiar en nombre sugerido; validar contenido.
            texto = temporal.read_text(encoding='utf-8-sig')
            xml = extraer_xml(texto)
            LOG.info('Archivo XML descargado y estructuralmente validado')
            return xml
        except PlaywrightTimeout as e:
            raise ResultadoIncierto('Enlace de archivo no produjo descarga verificable') from e
        finally:
            temporal.unlink(missing_ok=True)
    # Para bloques de codigo, texto del DOM suele ser XML sin el Markdown.
    bloques = nodo.locator('pre code')
    if bloques.count():
        validos = []
        for codigo in bloques.all_text_contents():
            try:
                validos.append(extraer_xml(codigo))
            except SalidaSinXml:
                pass
        if len(validos) == 1:
            LOG.info('XML extraido de bloque de codigo')
            return validos[0]
        if len(validos) > 1:
            raise ResultadoIncierto('Respuesta tiene multiples bloques XML validos')
    return extraer_xml(nodo.inner_text())


def _procesar_uno(page, config, original, temporal):
    primero = _enviar(page, config, INICIAL, original)
    try:
        return _leer_resultado(page, primero, config, temporal)
    except SalidaSinXml:
        LOG.warning('Primera respuesta finalizada, sin XML: solicitando una sola recuperacion')
    segundo = _enviar(page, config, RECUPERAR)
    return _leer_resultado(page, segundo, config, temporal)


def procesar_registros(origen: Path, config: dict, base: Path, limite=1):
    if limite not in (1, 2):
        raise ValueError('Modo de prueba: --limite debe ser 1 o 2')
    datos = json.loads(origen.read_text(encoding='utf-8'))
    registros = validar(datos['registros'])
    ruta = base / 'exportaciones' / ('procesados_' + origen.stem + '.json')
    if ruta.exists():
        trabajos = json.loads(ruta.read_text(encoding='utf-8'))['registros']
        if [x['id_solicitud'] for x in trabajos] != [x['id_solicitud'] for x in registros]:
            raise ValueError('Checkpoint no corresponde a esta exportacion')
    else:
        trabajos = [dict(x) for x in registros]
        escribir_json(ruta, trabajos)

    pendientes = [(i, r) for i, r in enumerate(trabajos) if not r.get('estado')]
    elegidos = pendientes[:limite]
    if not elegidos:
        LOG.info('No hay pendientes; checkpoint=%s', ruta)
        return ruta
    url = gpt_url(config)
    LOG.info('Seleccionados %s de %s registros pendientes; destino=GPT HERO', len(elegidos), len(pendientes))
    confirmacion = input(f'Procesar {len(elegidos)} XML(s) con HERO? Escribe PROCESAR: ')
    if confirmacion != 'PROCESAR':
        LOG.info('Ejecucion cancelada antes de abrir ChatGPT')
        return ruta
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        # Chrome avisa al iniciar sin sandbox; habilitar sandbox cuando sea viable.
        ctx = p.chromium.launch_persistent_context(str(CHROME_PROFILE), channel='chrome',
                                                   chromium_sandbox=True, headless=False,
                                                   accept_downloads=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            for n, (indice, registro) in enumerate(elegidos, 1):
                base_archivo = nombre_seguro(registro['id_n5'], registro['nombre_proceso'])
                final = base / 'corregidos' / (base_archivo + '.xml')
                original = base / 'originales' / (base_archivo + '.xml')
                temporal = base / 'temporales' / (base_archivo + '.xml')
                LOG.info('Registro %s/%s, solicitud=%s: preparando', n, len(elegidos), registro['id_solicitud'])
                if final.exists():
                    registro.update(estado='requiere_revision', motivo='Archivo final preexistente; revisar sin sobrescribir')
                    escribir_json(ruta, trabajos)
                    continue
                try:
                    original.parent.mkdir(parents=True, exist_ok=True)
                    if original.exists() and original.read_text(encoding='utf-8') != registro['xml']:
                        raise ResultadoIncierto('El XML original local difiere de la exportacion')
                    if not original.exists():
                        original.write_text(registro['xml'], encoding='utf-8')
                    page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    _esperar_editor(page, config)
                    LOG.info('GPT HERO abierto, editor listo; inicio de chat nuevo')
                    registro['estado'] = 'envio_iniciado'
                    escribir_json(ruta, trabajos)
                    texto = _procesar_uno(page, config, original, temporal)
                    xml_final = corregir_lane(texto, registro['id_n5'], registro['nombre_proceso'])
                    final.parent.mkdir(parents=True, exist_ok=True)
                    with final.open('x', encoding='utf-8') as f:
                        f.write(xml_final)
                    registro.update(estado='correccion_finalizada', id_archivo_n5=base_archivo,
                                    xml_corregido_ruta=str(final))
                    registro.pop('motivo', None)
                    LOG.info('Registro %s: XML corregido, LanePrincipal validado, archivo guardado', n)
                except SalidaSinXml as e:
                    registro.update(estado='correccion_fallida', motivo=str(e))
                    LOG.warning('Registro %s: segundo intento sin XML valido', n)
                except Exception as e:
                    registro.update(estado='requiere_revision', motivo=type(e).__name__)
                    LOG.error('Registro %s requiere revision, fase=%s (sin reenviar): %s', n,
                              registro.get('estado'), type(e).__name__)
                finally:
                    temporal.unlink(missing_ok=True)
                    escribir_json(ruta, trabajos)
            LOG.info('Checkpoint guardado: %s', ruta)
            if config.get('mantener_chrome_abierto', True):
                input('Chrome queda abierto para revisar. Pulsa Enter cuando quieras cerrarlo...')
        finally:
            ctx.close()
    return ruta
