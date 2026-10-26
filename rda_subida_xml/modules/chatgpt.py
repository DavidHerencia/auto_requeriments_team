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
from collections import Counter
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
TURNO = '[data-chatgpt-search-unit-key$=":assistant"]'
TURNO_ALTERNATIVO = '[data-content-search-unit-key$=":assistant"]'
TURNO_LEGACY = '[data-turn-key$=":assistant"]'
TURNO_LEGACY_2 = '[data-chatgpt-turn-key$=":assistant"]'
TURNO_ROLE = '[data-conversation-role="assistant"]'
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


def _respuestas_asistente(page, config):
    """Selector REAL: contenedor del mensaje, no su encabezado sr-only.

    En HTMLs validados, [data-conversation-role=assistant] es un h4
    cuyo unico texto es 'ChatGPT dijo:', no el mensaje.
    Configuracion anterior se migra sin editar config.local.json.
    """
    custom = config.get('gpt_respuesta_selector', '')
    historicos = (TURNO_LEGACY, TURNO_LEGACY_2, TURNO_ROLE, TURNO, TURNO_ALTERNATIVO)
    if custom and custom not in historicos:
        return page.locator(custom)
    return page.locator(TURNO)


def _marcadores_generacion(page, turno):
    """Observa señales reales del turno y el composer.

    data-state="closed" sobre Iniciar Voz pertenece al estado de su tooltip:
    NO demuestra por sí mismo que ChatGPT terminó de generar.
    """
    estado = turno.evaluate(
        """el => {
            const atributos = ['data-talvt-turn-state', 'data-turn-state', 'data-chatgpt-turn-state'];
            for (const nombre of atributos) {
                const propio = el.getAttribute(nombre);
                if (propio) return propio;
                const padre = el.closest('[' + nombre + ']');
                if (padre) return padre.getAttribute(nombre);
                const hijo = el.querySelector('[' + nombre + ']');
                if (hijo) return hijo.getAttribute(nombre);
            }
            return '';
        }"""
    )
    detener_por_testid = page.locator('[data-testid="stop-button"]:visible')
    detener_por_nombre = page.get_by_role(
        'button', name=re.compile(r'detener|stop|parar|cancel generation', re.I)
    )
    generando = detener_por_testid.count() > 0 or detener_por_nombre.count() > 0

    # Durante una generación extensa el composer presenta "Detener".
    # Al terminar reaparece "Iniciar Voz" (visible en el DOM inspeccionado).
    boton_enviar = page.locator(ENVIAR)
    enviar_habilitado = (
        boton_enviar.count() == 1
        and boton_enviar.first.is_visible()
        and boton_enviar.first.is_enabled()
    )
    voz = page.locator(
        'button[aria-label="Iniciar Voz"]:visible, '
        'button[aria-label="Start voice"]:visible, '
        'button[aria-label="Start Voice"]:visible'
    )
    composer_listo = enviar_habilitado or voz.count() > 0
    return estado == 'complete', generando, composer_listo


def _puede_cerrar_respuesta(completo, generando, generacion_vista, composer_listo):
    """Solo señales terminales: nunca solo el XML visible o tiempo estable.

    Si no se observó detener durante la generación, se requiere el
    estado explícito "complete", para evitar guardar una respuesta parcial.
    """
    return not generando and (completo or (generacion_vista and composer_listo))


def _nuevo_turno(page, config, anteriores, usuarios_antes):
    """Respuesta nueva + finalizacion observable + DOM asentado.

    El conteo de turnos de usuario NO es condicion necesaria: la version
    actual de HERO puede mostrar el mensaje sin data-turn-key='...:user'.
    """
    limite = time.monotonic() + int(config.get('timeout_respuesta_ms', 480000)) / 1000
    visto_respuesta = False
    generacion_vista = False
    estado_anterior = None
    firma_anterior = None
    lecturas_finales_iguales = 0
    ultimo_log = 0
    sin_selector_reportado = False

    while time.monotonic() < limite:
        respuestas = _respuestas_asistente(page, config)
        cantidad = respuestas.count()
        if not cantidad and not sin_selector_reportado:
            LOG.info('Esperando contenedor de mensaje de HERO: %s', TURNO)
            sin_selector_reportado = True
        if cantidad > anteriores + 1:
            raise ResultadoIncierto('Multiples respuestas nuevas: captura ambigua')

        if cantidad == anteriores + 1:
            turno = respuestas.nth(anteriores)
            if not visto_respuesta:
                LOG.info('Mensaje NUEVO del asistente detectado; contenedor de contenido localizado')
                visto_respuesta = True
            try:
                completo, generando, composer_listo = _marcadores_generacion(page, turno)
                generacion_vista |= generando
                estado = (completo, generando, composer_listo)
                if estado != estado_anterior:
                    LOG.info('Estado generacion: turno_complete=%s detener_visible=%s composer_listo=%s',
                             completo, generando, composer_listo)
                    estado_anterior = estado

                if _puede_cerrar_respuesta(completo, generando, generacion_vista, composer_listo):
                    # El evento (turno completo o reaparicion de Enviar tras
                    # detenerse la generacion) dispara el cierre. Dos lecturas
                    # iguales comprueban que termino el renderizado del DOM.
                    cuerpo = turno.locator('[data-markdown-text-style="assistant-message"]')
                    fuente = cuerpo.first if cuerpo.count() == 1 else turno
                    texto = fuente.inner_text(timeout=3000).strip()
                    codigo = fuente.locator('pre code').all_text_contents()
                    archivos = turno.locator('button[aria-label="Descargar archivo"], a[download], a[href$=".xml"]').count()
                    # Si la generacion ha terminado pero el contenido aun esta
                    # montandose, esperar su aparicion en el DOM, no reenviar.
                    if not texto and not codigo and not archivos:
                        firma_anterior = None
                        lecturas_finales_iguales = 0
                        page.wait_for_timeout(200)
                        continue
                    firma = (texto, tuple(codigo))
                    if firma == firma_anterior:
                        lecturas_finales_iguales += 1
                    else:
                        firma_anterior = firma
                        lecturas_finales_iguales = 0

                    if lecturas_finales_iguales >= 1 and texto:
                        if re.search(
                            r'No se ha podido cargar esta respuesta|Something went wrong|Unable to load this response',
                            texto, re.I
                        ):
                            raise ResultadoIncierto('ChatGPT muestra error de carga de respuesta')
                        LOG.info('Respuesta COMPLETA: caracteres=%s bloques_codigo=%s',
                                 len(texto), len(codigo))
                        return turno
                else:
                    firma_anterior = None
                    lecturas_finales_iguales = 0
            except ResultadoIncierto:
                raise
            except Exception as exc:
                LOG.debug('Turno en renderizado: tipo=%s', type(exc).__name__)

        if time.monotonic() - ultimo_log >= 10:
            usuarios = page.locator('[data-chatgpt-turn-key$=":user"], [data-turn-key$=":user"]').count()
            LOG.info(
                'Esperando fin de generacion: respuesta_nueva=%s user_turns=%s/%s generacion_vista=%s',
                visto_respuesta, usuarios, usuarios_antes, generacion_vista
            )
            ultimo_log = time.monotonic()
        page.wait_for_timeout(350)

    raise ResultadoIncierto('Timeout sin confirmacion de respuesta completa; no reenviar')


def instalar_diagnostico(page):
    """Cuenta fallos de red; no imprime cada request ni URLs sensibles."""
    conteos = Counter()

    def fallo(respuesta):
        try:
            host = urlsplit(respuesta.url).hostname or ''
            tipo = respuesta.request.resource_type
            if host.endswith(('chatgpt.com', 'openai.com')) and tipo in ('fetch', 'xhr', 'document') and respuesta.status >= 400:
                conteos[f'HTTP {respuesta.status} ({tipo})'] += 1
        except Exception:
            pass

    def error_red(solicitud):
        try:
            host = urlsplit(solicitud.url).hostname or ''
            if host.endswith(('chatgpt.com', 'openai.com')):
                motivo = str(solicitud.failure or '')
                codigo = re.search(r'net::[A-Z_0-9]+', motivo)
                conteos[f'RED {codigo.group(0) if codigo else "desconocida"}'] += 1
        except Exception:
            pass

    page.on('response', fallo)
    page.on('requestfailed', error_red)

    def resumen():
        if conteos:
            detalle = ', '.join(f'{clave}={cantidad}' for clave, cantidad in sorted(conteos.items()))
            LOG.warning('Resumen de solicitudes rechazadas/abortadas: %s', detalle)
            LOG.info('Los codigos HTTP no identifican por si solos la causa o el endpoint')
        else:
            LOG.info('Sin errores HTTP/red observados en este periodo')

    return resumen


def registrar_flags_chrome(contexto):
    """Comprobar flag inseguro sin imprimir linea de comandos ni perfil."""
    pagina = contexto.new_page()
    try:
        pagina.goto('chrome://version/', wait_until='domcontentloaded', timeout=10000)
        flags = pagina.locator('#command_line').inner_text(timeout=3000)
        LOG.info('Chrome: flag --no-sandbox detectado=%s', '--no-sandbox' in flags)
    except Exception:
        LOG.info('Chrome: flags de lanzamiento no disponibles')
    finally:
        pagina.close()


def _enviar(page, config, prompt, archivo=None):
    """Un solo Enter como en el prototipo; respuesta nueva confirma el envio."""
    editor = _esperar_editor(page, config)
    anteriores = _respuestas_asistente(page, config).count()
    usuarios = page.locator('[data-chatgpt-turn-key$=":user"], [data-turn-key$=":user"]').count()
    if archivo:
        _esperar_adjunto(page, archivo, config)
    editor.fill(prompt)
    LOG.info('Prompt escrito; caracteres=%s adjunto=%s', len(prompt), bool(archivo))
    try:
        editor.press('Enter', timeout=20000)
    except Exception as exc:
        LOG.error('Fallo Enter; envio incierto: tipo=%s', type(exc).__name__)
        raise ResultadoIncierto('No se pudo pulsar Enter de forma verificable') from exc
    LOG.info('Enter pulsado una sola vez; observando respuesta NUEVA')
    # No bloquear esperando [data-turn-key$=":user"]: en HERO el mensaje
    # visible se envia aunque ese atributo no aparezca. La respuesta nueva
    # constituye la confirmacion del envio.
    return _nuevo_turno(page, config, anteriores, usuarios)


def _leer_resultado(page, nodo, config, temporal):
    """Extrae SOLO del mensaje nuevo, en dos formatos HTML reales de HERO.

    1. Boton aria-label="Descargar archivo" (no es un enlace <a>).
    2. pre > code, cuyo textContent une los spans de resaltado.
    """
    contenedor = nodo.locator('[data-markdown-text-style="assistant-message"]')
    if contenedor.count() != 1:
        raise ResultadoIncierto(
            'El contenedor del mensaje no es unico o aun no esta disponible'
        )
    cuerpo = contenedor.first

    selector_archivo = config.get('gpt_descarga_selector') or (
        'button[aria-label="Descargar archivo"], a[download], a[href$=".xml"]'
    )
    # El boton de archivo esta DENTRO del mensaje, pero FUERA de Markdown.
    descargas = nodo.locator(selector_archivo)
    cantidad = descargas.count()
    if cantidad > 1:
        raise ResultadoIncierto('Varias descargas posibles en la misma respuesta')
    if cantidad == 1:
        temporal.parent.mkdir(parents=True, exist_ok=True)
        LOG.info('Respuesta con boton de archivo: solicitando descarga')
        try:
            with page.expect_download(timeout=int(config.get('timeout_descarga_ms', 30000))) as info:
                descargas.first.click()
            archivo = info.value
            archivo.save_as(temporal)
            xml = extraer_xml(temporal.read_text(encoding='utf-8-sig'))
            LOG.info('Archivo descargado y XML validado; caracteres=%s', len(xml))
            return xml
        except PlaywrightTimeout as exc:
            raise ResultadoIncierto('El boton de archivo no produjo una descarga verificable') from exc
        finally:
            temporal.unlink(missing_ok=True)

    bloques = cuerpo.locator('pre code')
    encontrados = []
    for texto in bloques.all_text_contents():
        try:
            encontrados.append(extraer_xml(texto))
        except SalidaSinXml:
            continue
    if len(encontrados) == 1:
        LOG.info('XML completo recuperado de pre code; caracteres=%s', len(encontrados[0]))
        return encontrados[0]
    if len(encontrados) > 1:
        raise ResultadoIncierto('Varios XML validos en bloques de codigo de la respuesta')
    if bloques.count():
        # Existe codigo, pero no se obtuvo XML utilizable: si se debe
        # pedir recuperacion al GPT, no se inventa un XML parcial.
        raise SalidaSinXml('La respuesta contiene codigo, pero no un XML valido')
    return extraer_xml(cuerpo.inner_text())


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
            registrar_flags_chrome(ctx)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            resumir_red = instalar_diagnostico(page)
            interrumpido = False
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
                except KeyboardInterrupt:
                    interrumpido = True
                    registro.update(
                        estado='requiere_revision',
                        motivo='Interrumpido manualmente; revisar chat antes de cualquier reenvio'
                    )
                    LOG.warning('Ctrl+C recibido: se conserva checkpoint y se detiene el lote')
                except Exception as e:
                    registro.update(estado='requiere_revision', motivo=type(e).__name__)
                    LOG.error('Registro %s requiere revision, fase=%s (sin reenviar): %s', n,
                              registro.get('estado'), type(e).__name__)
                finally:
                    temporal.unlink(missing_ok=True)
                    escribir_json(ruta, trabajos)
                if interrumpido:
                    break
            LOG.info('Checkpoint guardado: %s', ruta)
            resumir_red()
            if not interrumpido and config.get('mantener_chrome_abierto', True):
                input('Chrome queda abierto para revisar. Pulsa Enter cuando quieras cerrarlo...')
        finally:
            ctx.close()
    return ruta
