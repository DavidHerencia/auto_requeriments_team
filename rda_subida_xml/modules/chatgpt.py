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
from modules.session import esperar_sesion

LOG = logging.getLogger(__name__)
MENSAJE_INCOMPLETO = 'incompleto - no se puede generar porque falta demasiada info'
REGLA_INCOMPLETO = (
    'Si el XML esta incompleto, intenta completarlo usando la informacion disponible, '
    'sin inventar datos del proceso. Si falta demasiada informacion y es imposible '
    'generarlo, responde UNICAMENTE: "'
    + MENSAJE_INCOMPLETO + '".'
)
INICIAL = (
    'Corrige el XML adjunto usando y siguiendo las instrucciones de '
    '@BPMN ARIS XML Repair. Dame solamente el XML listo para descargar '
    'sin nada mas o, en caso de no poder, ponmelo completo para copiar y pegar. '
    + REGLA_INCOMPLETO
)
RECUPERAR = (
    'Dame solamente el XML listo para descargar sin nada mas o, '
    'en caso de no poder, ponmelo completo para copiar y pegar. '
    + REGLA_INCOMPLETO
    + ' Si no tienes otra opcion disponible, si, dame el XML para copiar y yo lo pego.'
)

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


class RespuestaIncompleta(ValueError):
    """HERO indico explicitamente que faltan demasiados datos; no reintentar."""


def es_respuesta_incompleta(texto):
    """Detecta el mensaje acordado, sin marcar como incompleto un XML real."""
    limpio = re.sub(r'\s+', ' ', texto).strip().strip(' *' + chr(96) + '"\'' + '.!')
    limpio = limpio.casefold().replace('–', '-').replace('—', '-')
    return limpio == MENSAJE_INCOMPLETO


class ResultadoIncierto(RuntimeError):
    """No se pudo verificar resultado; prohibido reintentar envio automaticamente."""

class ErrorPostprocesamiento(ValueError):
    """XML valido capturado, pero no se pudo aplicar la modificacion BPMN."""


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
    """Edita UNICAMENTE el name de la lane principal sin reserializar XML.

    Variantes observadas: LanePrincipal y Lane_Principal. Exige que
    exactamente UNA de ellas corresponda a un elemento BPMN <lane>.
    """
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as exc:
        raise ErrorPostprocesamiento('El XML capturado dejo de ser bien formado') from exc
    permitidas = {'LanePrincipal', 'Lane_Principal'}
    lanes = [
        x for x in raiz.iter()
        if x.tag.rsplit('}', 1)[-1] == 'lane'
        and x.attrib.get('id') in permitidas
    ]
    if len(lanes) != 1:
        raise ErrorPostprocesamiento(
            f'Se esperaba una sola lane principal ({", ".join(sorted(permitidas))}); '
            f'se encontraron {len(lanes)}'
        )
    id_lane = lanes[0].attrib['id']

    # Cambio textual mínimo para mantener intacta la serialización BPMN.
    tags = [
        m for m in re.finditer(r'<([A-Za-z_][\w.:-]*)\b[^>]*>', texto, flags=re.S)
        if m.group(1).rsplit(':', 1)[-1] == 'lane'
        and re.search(r'\bid\s*=\s*(["\x27])' + re.escape(id_lane) + r'\1', m.group())
    ]
    if len(tags) != 1:
        raise ErrorPostprocesamiento(f'No se identifico exactamente la etiqueta XML {id_lane}')
    tag_match = tags[0]
    atributos = list(re.finditer(r'\bname\s*=\s*(["\x27]).*?\1', tag_match.group(), flags=re.S))
    if len(atributos) != 1:
        raise ErrorPostprocesamiento(f'La lane {id_lane} no tiene exactamente un atributo name')

    atributo = atributos[0]
    nombre = f'{id_n5} {nombre_proceso}'
    etiqueta = (
        tag_match.group()[:atributo.start()]
        + 'name=' + quoteattr(nombre)
        + tag_match.group()[atributo.end():]
    )
    resultado = texto[:tag_match.start()] + etiqueta + texto[tag_match.end():]

    try:
        actualizado = ET.fromstring(resultado)
    except ET.ParseError as exc:
        raise ErrorPostprocesamiento('XML invalido despues de modificar la lane') from exc
    verificadas = [
        x for x in actualizado.iter()
        if x.tag.rsplit('}', 1)[-1] == 'lane'
        and x.attrib.get('id') == id_lane
    ]
    if len(verificadas) != 1 or verificadas[0].attrib.get('name') != nombre:
        raise ErrorPostprocesamiento('No se verifico el nuevo nombre de la lane principal')
    LOG.info('Postprocesamiento XML correcto: lane_id=%s', id_lane)
    return resultado


def corregir_nombres_bpmn(texto, id_n5, nombre_proceso):
    """Normaliza process.name y lane principal.name, sin reserializar XML.

    Exige exactamente un BPMN process y una lane principal, ubicada DENTRO de
    ese proceso. Conserva IDs, namespaces, etiquetas y el resto del XML.
    """
    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError as exc:
        raise ErrorPostprocesamiento('XML de entrada invalido') from exc

    procesos = [x for x in raiz.iter() if x.tag.rsplit('}', 1)[-1] == 'process']
    if len(procesos) != 1:
        raise ErrorPostprocesamiento(f'Se esperaba un unico BPMN process; encontrados {len(procesos)}')
    proceso = procesos[0]
    lanes = [
        x for x in proceso.iter()
        if x.tag.rsplit('}', 1)[-1] == 'lane'
        and x.attrib.get('id') in {'LanePrincipal', 'Lane_Principal'}
    ]
    if len(lanes) != 1:
        raise ErrorPostprocesamiento('No se encontro una unica lane principal dentro del proceso')

    nombre = f'{id_n5} {nombre_proceso}'
    # Aprovecha el cambio minimo de lane, previamente validado.
    editado = corregir_lane(texto, id_n5, nombre_proceso)

    # Buscar solamente el tag de inicio BPMN <process>, no definitions ni
    # documentacion; tolerar atributos entrecomillados con > en el texto.
    tags = re.finditer(
        r'<(?P<tag>[A-Za-z_][\w.:-]*)(?:[^>"\x27]|"[^"]*"|\x27[^\x27]*\x27)*>',
        editado, flags=re.S
    )
    procesos_tags = [
        match for match in tags
        if match.group('tag').rsplit(':', 1)[-1] == 'process'
    ]
    if len(procesos_tags) != 1:
        raise ErrorPostprocesamiento('No se pudo localizar un unico tag process')
    match = procesos_tags[0]
    atributos = list(re.finditer(
        r'(?<![\w:.-])name\s*=\s*(?:"[^"]*"|\x27[^\x27]*\x27)',
        match.group(), flags=re.S
    ))
    if len(atributos) != 1:
        raise ErrorPostprocesamiento('El tag process requiere un unico atributo name')
    atributo = atributos[0]
    etiqueta = (
        match.group()[:atributo.start()]
        + 'name=' + quoteattr(nombre)
        + match.group()[atributo.end():]
    )
    resultado = editado[:match.start()] + etiqueta + editado[match.end():]

    try:
        verificado = ET.fromstring(resultado)
    except ET.ParseError as exc:
        raise ErrorPostprocesamiento('XML invalido tras normalizar nombres') from exc
    proc_final = [x for x in verificado.iter() if x.tag.rsplit('}', 1)[-1] == 'process']
    lanes_final = [
        x for x in proc_final[0].iter()
        if x.tag.rsplit('}', 1)[-1] == 'lane'
        and x.attrib.get('id') in {'LanePrincipal', 'Lane_Principal'}
    ] if len(proc_final) == 1 else []
    if (
        len(proc_final) != 1
        or len(lanes_final) != 1
        or proc_final[0].attrib.get('name') != nombre
        or lanes_final[0].attrib.get('name') != nombre
    ):
        raise ErrorPostprocesamiento('No se verificaron ambos nombres BPMN')
    LOG.info('Postprocesamiento XML correcto: process.name y lane.name sincronizados')
    return resultado


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
    """Enviar por el botón real, UNA vez, y confirmar transición observable.

    Con menciones @ de skills, Enter puede escoger un elemento del menú
    en vez de enviar el mensaje. Por eso nunca se usa Enter para enviar.
    Un click sin respuesta tampoco garantiza envío: comprobar el DOM.
    """
    editor = _esperar_editor(page, config)
    anteriores = _respuestas_asistente(page, config).count()
    usuarios = page.locator(
        '[data-chatgpt-turn-key$=":user"], [data-turn-key$=":user"]'
    ).count()
    if archivo:
        _esperar_adjunto(page, archivo, config)
    editor.fill(prompt)
    LOG.info('Prompt escrito; caracteres=%s adjunto=%s', len(prompt), bool(archivo))

    timeout = int(config.get('timeout_envio_ms', 30000))
    selector_boton = _selector(config, 'gpt_enviar_selector', ENVIAR)
    boton = page.locator(selector_boton)
    try:
        boton.wait_for(state='visible', timeout=timeout)
        if boton.count() != 1:
            raise ResultadoIncierto(
                f'Botón Enviar ambiguo: se encontraron {boton.count()} controles'
            )
        # Playwright espera automáticamente habilitación/visibilidad.
        # Sólo este click puede iniciar el envío: no hay Enter de respaldo.
        boton.click(timeout=timeout)
        LOG.info('Click único en botón Enviar; verificando aceptación del mensaje')
    except ResultadoIncierto:
        raise
    except Exception as exc:
        LOG.warning('Envío GPT no confirmado: botón Enviar no accionable (%s)',
                    type(exc).__name__)
        raise ResultadoIncierto(
            'No se pudo accionar el botón Enviar con certeza; no reenviar'
        ) from exc

    # Esperar un evento de UI real: el editor se vacía o comienza una nueva
    # respuesta de HERO. El botón pulsado por sí solo NO es confirmación.
    selector_editor = _selector(config, 'gpt_editor_selector', EDITOR)
    selector_respuestas = config.get('gpt_respuesta_selector', '')
    if not selector_respuestas or selector_respuestas in (
        TURNO_LEGACY, TURNO_LEGACY_2, TURNO_ROLE, TURNO, TURNO_ALTERNATIVO
    ):
        selector_respuestas = TURNO
    try:
        senal = page.wait_for_function(
            """({editorSelector, responseSelector, previous}) => {
                const respuestas = document.querySelectorAll(responseSelector).length;
                if (respuestas > previous) return 'respuesta_nueva';
                const editores = [...document.querySelectorAll(editorSelector)];
                if (editores.length !== 1) return false;
                const texto = (editores[0].innerText || editores[0].textContent || '')
                    .replace(/[\\u200b\\u200c\\u200d]/g, '').trim();
                return texto === '' ? 'editor_vaciado' : false;
            }""",
            arg={
                'editorSelector': selector_editor,
                'responseSelector': selector_respuestas,
                'previous': anteriores
            },
            timeout=timeout
        ).json_value()
    except PlaywrightTimeout as exc:
        LOG.error('ChatGPT no confirmó transición del editor tras click Enviar')
        raise ResultadoIncierto(
            'Después de Enviar, el editor no se vació y no apareció respuesta; '
            'no se reenviará automáticamente'
        ) from exc
    LOG.info('GPT: señal posterior al clic observada: %s; esperando respuesta', senal)
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
    if es_respuesta_incompleta(cuerpo.inner_text()):
        raise RespuestaIncompleta(MENSAJE_INCOMPLETO)

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


def procesar_registros(origen: Path, config: dict, base: Path, limite=1,
                       solicitudes=None, reintentar=False, intento='inicial',
                       confirmar=True):
    """Lote GPT; reintentos desde original sin pisar versiones anteriores."""
    if limite is not None and (not isinstance(limite, int) or limite < 1):
        raise ValueError('--limite debe ser positivo o None para todo el lote')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', intento):
        raise ValueError('Etiqueta de intento inválida')
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

    permitidos = set(solicitudes) if solicitudes is not None else None
    if permitidos is not None:
        presentes = {r['id_solicitud'] for r in trabajos}
        if not permitidos.issubset(presentes):
            raise ValueError('Solicitud ausente en la exportación')
    seleccionados = [(i, r) for i, r in enumerate(trabajos)
                     if permitidos is None or r['id_solicitud'] in permitidos]
    elegibles_retry = {'correccion_fallida', 'incompleto', 'postprocesamiento_pendiente'}
    if intento.startswith('aris_revision_'):
        elegibles_retry.add('correccion_finalizada')
    pendientes = [(i, r) for i, r in seleccionados
                  if ((r.get('estado') in elegibles_retry
                       and r.get('intento_gpt') != intento)
                      if reintentar else not r.get('estado'))]
    elegidos = pendientes[:limite] if limite is not None else pendientes
    if not elegidos:
        LOG.info('No hay pendientes; checkpoint=%s', ruta)
        return ruta
    url = gpt_url(config)
    LOG.info('Seleccionados %s de %s registros pendientes; destino=GPT HERO', len(elegidos), len(pendientes))
    if confirmar:
        confirmacion = input(f'Procesar {len(elegidos)} XML(s) con HERO? Escribe PROCESAR: ')
        if confirmacion != 'PROCESAR':
            LOG.info('Ejecucion cancelada antes de abrir ChatGPT')
            return ruta
    if reintentar:
        for _, registro in elegidos:
            registro.setdefault('historial_gpt', []).append({
                'intento_anterior': registro.get('intento_gpt', 'inicial'),
                'estado': registro.get('estado', ''),
                'motivo': registro.get('motivo', ''),
                'xml_corregido_ruta': registro.get('xml_corregido_ruta')
            })
            for campo in ('estado', 'motivo', 'xml_corregido_ruta', 'xml_capturado_ruta'):
                registro.pop(campo, None)
        escribir_json(ruta, trabajos)
    subcarpeta = Path('') if intento == 'inicial' else Path('reintentos') / intento
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
                final = base / 'corregidos' / subcarpeta / (base_archivo + '.xml')
                original = base / 'originales' / (base_archivo + '.xml')
                temporal = base / 'temporales' / subcarpeta / (base_archivo + '.xml')
                capturado = base / 'capturados' / subcarpeta / (base_archivo + '.xml')
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
                    page.goto(url, wait_until='domcontentloaded',
                              timeout=int(config.get('timeout_ms', 120000)))
                    esperar_sesion(page, 'chatgpt', url, config,
                                  lambda: _esperar_editor(page, config))
                    LOG.info('GPT HERO abierto, editor listo; inicio de chat nuevo')
                    registro['intento_gpt'] = intento
                    registro['estado'] = 'envio_iniciado'
                    escribir_json(ruta, trabajos)
                    texto = _procesar_uno(page, config, original, temporal)
                    # Persistir INMEDIATAMENTE el XML ya validado antes de
                    # cualquier posprocesamiento que pudiera fallar.
                    capturado.parent.mkdir(parents=True, exist_ok=True)
                    if capturado.exists():
                        if capturado.read_text(encoding='utf-8') != texto:
                            raise ResultadoIncierto('Conflicto con XML capturado previamente')
                    else:
                        with capturado.open('x', encoding='utf-8') as f:
                            f.write(texto)
                    registro.update(
                        estado='xml_capturado', id_archivo_n5=base_archivo,
                        xml_capturado_ruta=str(capturado)
                    )
                    escribir_json(ruta, trabajos)
                    LOG.info('Registro %s: XML de HERO capturado y persistido (%s caracteres)', n, len(texto))

                    xml_final = corregir_nombres_bpmn(texto, registro['id_n5'], registro['nombre_proceso'])
                    final.parent.mkdir(parents=True, exist_ok=True)
                    with final.open('x', encoding='utf-8') as f:
                        f.write(xml_final)
                    registro.update(
                        estado='correccion_finalizada',
                        id_archivo_n5=base_archivo,
                        xml_corregido_ruta=str(final)
                    )
                    registro.pop('motivo', None)
                    LOG.info('Registro %s: process y lane actualizados; XML definitivo guardado', n)
                except RespuestaIncompleta as e:
                    registro.update(estado='incompleto', motivo=MENSAJE_INCOMPLETO)
                    LOG.warning('Registro %s: HERO indico informacion insuficiente; sin segundo envio', n)
                except SalidaSinXml as e:
                    registro.update(estado='correccion_fallida', motivo=str(e))
                    LOG.warning('Registro %s: respuesta de HERO sin XML recuperable: %s', n, e)
                except ErrorPostprocesamiento as e:
                    registro.update(
                        estado='postprocesamiento_pendiente',
                        motivo=str(e),
                        xml_capturado_ruta=str(capturado)
                    )
                    LOG.warning('Registro %s: XML capturado, requiere revisar la lane: %s', n, e)
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
            if not interrumpido and confirmar and config.get('mantener_chrome_abierto', True):
                input('Chrome queda abierto para revisar. Pulsa Enter cuando quieras cerrarlo...')
        finally:
            ctx.close()
    if interrumpido and not confirmar:
        # En el pipeline, Ctrl+C debe detener TODAS las etapas, incluida ARIS.
        # La fase GPT individual conserva el comportamiento anterior.
        raise KeyboardInterrupt('Procesamiento GPT interrumpido; pipeline detenido')
    return ruta
