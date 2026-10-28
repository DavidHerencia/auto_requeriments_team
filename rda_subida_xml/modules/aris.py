"""Importación ARIS por informe BBVA - Importar BPMN.

Solo opera en la carpeta configurada. Ningún selector cambia carpetas,
mueve modelos ni modifica el repositorio fuera de la importación solicitada.
"""
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

LOG = logging.getLogger(__name__)
REPORT = 'BBVA - Importar BPMN'
REPORTS_TAB = 'li[data-testid="REPORT_REPOSITORY_BUTTON"] a[aria-label="Informes"]'
REPORT_SELECTOR = '.tcReportSelectReportDropDown input[role="combobox"]'
LAUNCH = 'div.reportActionPanel a[aria-label="Lanzar"]'
DIALOG = 'div[role="dialog"][aria-hidden="false"]'
FILE_INPUT = 'input[type="file"][name="fileUpload"][accept*=".xml"]'
FILE_ERROR = 'El fichero seleccionado no es válido'
REPORT_DONE = 'El informe está completo'


class ArisNoConfirmado(RuntimeError):
    """El estado de la importación no puede determinarse con seguridad."""


def validar_xml_aris(ruta: Path):
    ruta = Path(ruta).expanduser().resolve()
    if not ruta.is_file() or ruta.suffix.lower() != '.xml':
        raise ValueError('La ruta debe apuntar a un archivo XML existente')
    # Validación sintáctica únicamente. La compatibilidad real la decide ARIS.
    ET.fromstring(ruta.read_bytes())
    return ruta


def _arise_url(cfg):
    url = cfg.get('aris_url', '').strip()
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.path:
        raise ValueError('aris_url debe ser una URL HTTPS completa de la carpeta ARIS')
    if '/repository/' not in url:
        raise ValueError('aris_url debe dirigir directamente a la carpeta de repositorio')
    return url


def _una_visible(locator, descripcion):
    candidatas = [loc for loc in locator.all() if loc.is_visible()]
    if len(candidatas) != 1:
        raise ArisNoConfirmado(f'{descripcion}: se esperó una opción visible; encontradas {len(candidatas)}')
    return candidatas[0]


def _opcion_informe_en_popup(elemento):
    """Excluir resultados históricos del informe; aceptar solo menú desplegado."""
    return elemento.evaluate("""el => {
        if (el.closest('.reportPanel-reports-list, .finished-report-item-row, .scrollable-report-panel')) {
            return false;
        }
        const rect = el.getBoundingClientRect();
        const style = getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 &&
               style.visibility !== 'hidden' && style.display !== 'none';
    }""")


def _abrir_informes(page, cfg):
    """ARIS GWT conserva el panel en el DOM incluso cuando está oculto.

    La señal de apertura es VISIBILIDAD REAL del panel e input,
    NO su existencia ni el atributo 'active' por sí solo.
    """
    panel = page.locator('.sidebar.reportSidebar')
    combo = page.locator(REPORT_SELECTOR)
    tab = page.locator('li[data-testid="REPORT_REPOSITORY_BUTTON"]')
    timeout = int(cfg.get('aris_timeout_seleccion_ms', 20000))
    try:
        tab.locator('a[aria-label="Informes"]').wait_for(state='visible', timeout=timeout)
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('No apareció el botón Informes del repositorio') from exc
    if tab.count() != 1:
        raise ArisNoConfirmado('Hay más de una pestaña Informes')
    # El campo puede crearse únicamente DESPUÉS del clic en Informes.
    if panel.count() and panel.is_visible() and combo.count() and combo.is_visible():
        LOG.info('ARIS: panel Informes ya visible; no alternar pestaña activa')
        return combo

    # Es normal encontrar el input en el DOM aunque el panel esté oculto.
    # Si la pestaña aún no está activa, UN clic abre el panel.
    if 'active' not in (tab.get_attribute('class') or '').split():
        LOG.info('ARIS: abriendo Informes con clic en el icono de barras')
        tab.locator('a[aria-label="Informes"]').click()
    else:
        LOG.info('ARIS: pestaña marcada activa; esperando que aparezca el panel')

    try:
        panel.wait_for(state='visible', timeout=timeout)
        combo.wait_for(state='visible', timeout=timeout)
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado(
            'Panel Informes oculto: no reintento el clic sobre una pestaña posiblemente activa'
        ) from exc
    if combo.count() != 1:
        raise ArisNoConfirmado('Selector de informes ambiguo tras abrir el panel')
    LOG.info('ARIS: panel Informes visible y selector listo')
    return combo


def _contexto_actual_informes(page, cfg):
    """Esperar la carpeta REAL, no el placeholder transitorio de ARIS."""
    panel = page.locator('.sidebar.reportSidebar .reportContextPanel')
    timeout = int(cfg.get('aris_timeout_carpeta_ms', 120000))
    esperado = ' '.join((cfg.get('aris_grupo_informes') or '').split())
    modo = cfg.get('modo_aris', 'inspeccionar')
    panel.wait_for(state='visible', timeout=timeout)

    script = """esperado => {
        const panel = document.querySelector('.sidebar.reportSidebar .reportContextPanel');
        if (!panel) return false;
        const nombres = [...panel.querySelectorAll('.label')]
            .filter(el => {
                const r = el.getBoundingClientRect();
                const css = getComputedStyle(el);
                return r.width > 0 && r.height > 0 &&
                       css.visibility !== 'hidden' && css.display !== 'none';
            })
            .map(el => (el.textContent || '').replace(/\\s+/g, ' ').trim())
            .filter(Boolean);
        if (nombres.length !== 1) return false;
        const actual = nombres[0];
        if (/^ninguna selección de informe válida disponible$/i.test(actual))
            return false;
        if (esperado && actual.toLocaleLowerCase('es') !==
                        esperado.toLocaleLowerCase('es'))
            return false;
        return actual;
    }"""
    # En preparar una discrepancia es advertencia, nunca una importación.
    esperar = esperado if modo == 'ejecutar' else ''
    try:
        actual = page.wait_for_function(script, arg=esperar, timeout=timeout).json_value()
    except PlaywrightTimeout as exc:
        nombres = [' '.join(x.inner_text().split()) for x in panel.locator('.label').all()
                   if x.is_visible()]
        LOG.warning('ARIS: carpeta no lista después de %sms: actual=%s esperado=%r',
                    timeout, nombres, esperado)
        if esperado and modo == 'ejecutar' and nombres and nombres[0] != (
                'Ninguna selección de informe válida disponible'):
            raise ArisNoConfirmado(
                f'Carpeta incorrecta o carga incompleta: actual={nombres!r}, esperada={esperado!r}'
            ) from exc
        raise ArisNoConfirmado(
            'ARIS no confirmó la carpeta de Informes; no se lanzará el informe'
        ) from exc
    LOG.info('ARIS: contexto real del panel Informes: %r', actual)
    if esperado and actual.casefold() != esperado.casefold():
        LOG.warning('ARIS: carpeta esperada=%r, observada=%r; modo preparar, sin lanzar',
                    esperado, actual)
    return actual


def seleccionar_informe(page, cfg):
    """Seleccionar por clic la opción real del desplegable, NO el historial."""
    report = cfg.get('aris_report_name', REPORT)
    _abrir_informes(page, cfg)
    carpeta = _contexto_actual_informes(page, cfg)
    LOG.info('ARIS: grupo de informes identificado: %s', carpeta)

    # GWT: abrir la flecha; el historial inferior puede mostrar el MISMO
    # nombre, pero jamás debemos pulsar un informe ya ejecutado.
    desplegable = page.locator(
        '.tcReportSelectReportDropDown a[aria-label="dropdown-toggle"]'
    )
    if desplegable.count() != 1 or not desplegable.is_visible():
        raise ArisNoConfirmado('No se encontró el botón desplegable del informe')
    desplegable.click()
    LOG.info('ARIS: desplegable Informes abierto; buscando opción exacta')

    def coincidencias():
        candidatas = page.get_by_text(report, exact=True).all()
        visibles = [e for e in candidatas if e.is_visible()]
        menu = [e for e in visibles if _opcion_informe_en_popup(e)]
        return candidatas, visibles, menu

    # Esperar una OPCIÓN NUEVA real en el DOM, no que transcurran segundos.
    timeout = int(cfg.get('aris_timeout_seleccion_ms', 20000))
    try:
        page.wait_for_function(
            """nombre => Array.from(document.querySelectorAll('body *')).some(el => {
                if (el.children.length || (el.textContent || '').trim() !== nombre) return false;
                if (el.closest('.reportPanel-reports-list, .finished-report-item-row, .scrollable-report-panel')) return false;
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden';
            })""", arg=report, timeout=timeout
        )
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado(
            'No apareció la opción en el desplegable; revisar DOM abierto'
        ) from exc

    todas, visibles, menu = coincidencias()
    LOG.info(
        'ARIS: coincidencias del nombre exacto: total=%s visibles=%s fuera_del_historial=%s',
        len(todas), len(visibles), len(menu)
    )
    if len(menu) != 1:
        raise ArisNoConfirmado(
            f'Opción del desplegable ambigua; fuera del historial={len(menu)}'
        )
    menu[0].click()
    # La elección debe quedar en el combobox, no solo cerrar el popup.
    try:
        page.wait_for_function(
            """expected => {
                const x = document.querySelector(
                    'div.reportActionPanel .tcReportSelectReportDropDown input[role="combobox"]'
                );
                return !!x && x.value.trim() === expected;
            }""",
            arg=report,
            timeout=timeout
        )
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('El informe no quedó seleccionado en el campo') from exc
    LOG.info('ARIS: informe exacto seleccionado por clic: %s', report)
    return carpeta


def _dialogo_con(page, selector):
    """Encuentra el modal ACTIVO que contiene un control concreto."""
    return page.locator(DIALOG).filter(has=page.locator(selector))


def _aceptar(modal, descripcion):
    """Solo Aceptar dentro del modal activo, nunca un control de la página."""
    opcion = modal.locator('a[aria-label="Aceptar"], button[aria-label="Aceptar"], button:has-text("Aceptar")')
    opcion = _una_visible(opcion, descripcion)
    if opcion.get_attribute('aria-disabled') == 'true' or not opcion.is_enabled():
        raise ArisNoConfirmado(f'{descripcion}: botón inhabilitado')
    opcion.click()


def _modal_activo_con_texto(page, texto):
    """True solo si el modal solicitado está realmente visible."""
    return any(
        d.is_visible()
        for d in page.locator(DIALOG).filter(
            has=page.get_by_text(texto, exact=False)
        ).all()
    )


def _esperar_transicion_archivo(page, cfg):
    """Esperar EVENTO: opciones nuevas, rechazo o botón de fichero habilitado.

    GWT puede subir el fichero en onchange y avanzar automáticamente a
    Opciones de importación. En ese caso NO pulsar Aceptar de nuevo.
    """
    timeout = int(cfg.get('aris_timeout_paso_ms', 45000))
    script = """() => {
        const visible = el => {
            const r = el.getBoundingClientRect();
            const css = getComputedStyle(el);
            return r.width > 0 && r.height > 0 &&
                   css.visibility !== 'hidden' && css.display !== 'none';
        };
        const dialogos = [...document.querySelectorAll(
            'div[role="dialog"][aria-hidden="false"]'
        )].filter(visible);
        if (dialogos.some(d => (d.textContent || '').includes('Opciones de importación')))
            return 'opciones';
        if (dialogos.some(d => (d.textContent || '').includes('El fichero seleccionado no es válido')))
            return 'rechazo';
        const fichero = dialogos.find(d =>
            !!d.querySelector('input[type="file"][name="fileUpload"]')
        );
        if (!fichero) return false;
        const aceptar = [...fichero.querySelectorAll('a, button')]
            .find(el => visible(el) && (
                el.getAttribute('aria-label') === 'Aceptar' ||
                (el.textContent || '').trim() === 'Aceptar'
            ));
        if (aceptar && aceptar.getAttribute('aria-disabled') !== 'true' &&
            !aceptar.matches(':disabled'))
            return 'aceptar_archivo';
        return false;
    }"""
    try:
        return page.wait_for_function(script, timeout=timeout).json_value()
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado(
            'No avanzó la selección de archivo ni se habilitó Aceptar: revisar ventana ARIS'
        ) from exc


def adjuntar_xml(page, ruta, cfg=None):
    """Selecciona el XML una vez y observa cómo ARIS avanza.

    Retorna 'opciones' o 'rechazo'. Nunca confirma dos veces un formulario.
    """
    cfg = cfg or {}
    input_file = page.locator(FILE_INPUT)
    input_file.wait_for(state='attached')
    if input_file.count() != 1:
        raise ArisNoConfirmado('No se encontró un único input de archivo XML')
    dialogo = _dialogo_con(page, FILE_INPUT)
    if dialogo.count() != 1 or not dialogo.is_visible():
        raise ArisNoConfirmado('No se encontró el modal activo Seleccionar fichero XML')
    input_file.set_input_files(str(ruta))
    nombres = input_file.evaluate('el => Array.from(el.files || []).map(f => f.name)')
    if nombres != [ruta.name]:
        raise ArisNoConfirmado('El selector de archivos no confirmó el XML solicitado')
    LOG.info('Archivo seleccionado para ARIS (sin copiar su contenido): %s', ruta.name)

    senal = _esperar_transicion_archivo(page, cfg)
    if senal == 'opciones':
        LOG.info('ARIS avanzó automáticamente a Opciones de importación; sin segundo clic')
        return 'opciones'
    if senal == 'rechazo':
        LOG.warning('ARIS rechazó el fichero antes de mostrar opciones')
        return 'rechazo'
    if senal != 'aceptar_archivo':
        raise ArisNoConfirmado(f'Transición inesperada tras seleccionar XML: {senal}')

    # La página puede avanzar entre la detección y el clic. No usar jamás
    # un botón obsoleto ni repetir el clic si ARIS ya cambió de modal.
    if _modal_activo_con_texto(page, 'Opciones de importación'):
        LOG.info('ARIS cambió a Opciones de importación antes del clic; se omite')
        return 'opciones'
    if _modal_activo_con_texto(page, FILE_ERROR):
        LOG.warning('ARIS rechazó el XML antes del clic del archivo')
        return 'rechazo'
    if not dialogo.is_visible():
        raise ArisNoConfirmado('El diálogo del fichero desapareció sin resultado confirmado')
    try:
        _aceptar(dialogo, 'Aceptar archivo XML')
        LOG.info('ARIS: clic de confirmación del archivo realizado una vez')
    except Exception:
        if not (
            _modal_activo_con_texto(page, 'Opciones de importación') or
            _modal_activo_con_texto(page, FILE_ERROR)
        ):
            raise
        LOG.info('ARIS cambió de modal durante la confirmación; clic no repetido')

    # La segunda etapa tiene que estar visiblemente lista antes de continuar.
    resultado = _esperar_transicion_final_archivo(page, cfg)
    return resultado


def _esperar_transicion_final_archivo(page, cfg):
    timeout = int(cfg.get('aris_timeout_paso_ms', 45000))
    try:
        estado = page.wait_for_function(
            """() => {
                const visibles = [...document.querySelectorAll(
                    'div[role="dialog"][aria-hidden="false"]'
                )].filter(d => {
                    const r = d.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                });
                if (visibles.some(d => (d.textContent || '').includes('Opciones de importación')))
                    return 'opciones';
                if (visibles.some(d => (d.textContent || '').includes('El fichero seleccionado no es válido')))
                    return 'rechazo';
                return false;
            }""",
            timeout=timeout
        ).json_value()
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('No apareció Opciones de importación después de adjuntar XML') from exc
    if estado == 'opciones':
        LOG.info('ARIS: modal Opciones de importación confirmado')
    else:
        LOG.warning('ARIS: XML rechazado antes de abrir Opciones')
    return estado


def _normalizar_tipo_importacion(valor):
    """Ignorar solo tildes, mayúsculas y espacios; NO aceptar otro modo."""
    import unicodedata
    limpio = ''.join(
        c for c in unicodedata.normalize('NFKD', str(valor or ''))
        if not unicodedata.combining(c)
    )
    return ' '.join(limpio.casefold().split())


def _opcion_auto(page, cfg):
    """Confirmar el modo automático en el modal SCRIPT_DIALOG de ARIS.

    El combo GWT muestra 'Importación automatica' sin tilde, aunque el
    texto explicativo y nuestra configuración tienen 'automática'.
    Se comprueba la equivalencia normalizada antes de hacer clic.
    """
    timeout = int(cfg.get('aris_timeout_paso_ms', 45000))
    expected = cfg.get('aris_tipo_importacion', 'Importación automática')
    if _normalizar_tipo_importacion(expected) != 'importacion automatica':
        raise ArisNoConfirmado(
            f'Configuración no autorizada para importación automática: {expected!r}'
        )
    modal = page.locator(
        'div[role="dialog"][aria-hidden="false"][data-testid="SCRIPT_DIALOG"]'
    ).filter(has=page.get_by_text('Opciones de importación', exact=True))
    try:
        modal.wait_for(state='visible', timeout=timeout)
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('No apareció el modal Opciones de importación') from exc
    modal = _una_visible(modal, 'Modal de opciones de importación')
    combo = modal.locator('[data-testid="DROP_OPCIONES"] input[role="combobox"]')
    if combo.count() != 1:
        raise ArisNoConfirmado('No se encontró el combobox de tipo de importación')
    combo.wait_for(state='visible', timeout=timeout)
    try:
        page.wait_for_function(
            r"""valor => {
                const normal = t => String(t || '')
                    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
                    .replace(/\s+/g, ' ').trim().toLocaleLowerCase('es');
                const d = document.querySelector(
                    'div[role="dialog"][aria-hidden="false"][data-testid="SCRIPT_DIALOG"]'
                );
                const x = d && d.querySelector(
                    '[data-testid="DROP_OPCIONES"] input[role="combobox"]'
                );
                return !!x && normal(x.value) === normal(valor);
            }""",
            arg=expected,
            timeout=timeout
        )
    except PlaywrightTimeout as exc:
        LOG.warning('ARIS: modo de importación incorrecto o no cargado: actual=%r, esperado=%r',
                    combo.input_value() if combo.is_visible() else '(oculto)', expected)
        raise ArisNoConfirmado(
            'El tipo de importación no coincide con Importación automática; sin confirmar'
        ) from exc

    observado = combo.input_value()
    if _normalizar_tipo_importacion(observado) != _normalizar_tipo_importacion(expected):
        raise ArisNoConfirmado(
            f'El modo de importación cambió antes del clic: observado={observado!r}'
        )
    LOG.info('ARIS: modo de importación validado: UI=%r (equivale a %r)',
             observado, expected)
    aceptar = modal.locator('.modal-footer a[aria-label="Aceptar"]')
    if aceptar.count() != 1 or not aceptar.is_visible() or not aceptar.is_enabled() or (
        aceptar.get_attribute('aria-disabled') == 'true'
    ):
        raise ArisNoConfirmado('Aceptar opciones: botón no visible o deshabilitado')
    aceptar.click()
    LOG.info('ARIS: Opciones de importación aceptadas por clic; esperando resultado')


def clasificar_mensaje(texto):
    texto = ' '.join(texto.split())
    if FILE_ERROR in texto:
        return 'xml_rechazado'
    if REPORT_DONE in texto:
        return 'informe_completo'
    return 'requiere_revision'


def _resultado(page, cfg):
    """Espera modal terminal por evento del DOM; timeout solo como máximo."""
    timeout = int(cfg.get('aris_timeout_importacion_ms', 300000))
    try:
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll('div[role="dialog"][aria-hidden="false"]'))
                .some(d => {
                    const t = (d.textContent || '').replace(/\s+/g, ' ');
                    return t.includes('El informe está completo') ||
                           t.includes('El fichero seleccionado no es válido');
                })""",
            timeout=timeout
        )
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('No se observó modal de éxito o rechazo; verificar manualmente') from exc

    dialogs = page.locator(DIALOG)
    encontrados = []
    for dlg in dialogs.all():
        if dlg.is_visible():
            estado = clasificar_mensaje(dlg.inner_text())
            if estado != 'requiere_revision':
                encontrados.append((estado, dlg))
    if len(encontrados) != 1:
        raise ArisNoConfirmado('Resultado ARIS ambiguo, no se reenviará XML')
    estado, dlg = encontrados[0]
    if estado == 'xml_rechazado':
        LOG.warning('ARIS rechazó el archivo XML (mensaje genérico)')
    else:
        LOG.info('ARIS confirmó finalización del informe de importación')
    _cerrar_popup_resultado(dlg, estado, cfg)
    return {'estado': estado, 'importado': None if estado == 'informe_completo' else False,
            'mensaje': FILE_ERROR if estado == 'xml_rechazado' else REPORT_DONE}


def _cerrar_popup_resultado(dialogo, estado, cfg):
    """Cerrar el mensaje terminal ya clasificado (éxito o rechazo) una sola vez.

    El HTML real usa un message-dialog y un enlace Aceptar en modal-footer.
    Se vuelve a comprobar el estado antes del clic para no aceptar otro modal.
    """
    if not dialogo.is_visible() or clasificar_mensaje(dialogo.inner_text()) != estado:
        raise ArisNoConfirmado('El popup final cambió antes de aceptar; revisar ARIS')
    aceptar = dialogo.locator('.modal-footer a[aria-label="Aceptar"]')
    if aceptar.count() != 1 or not aceptar.is_visible() or not aceptar.is_enabled() or (
        aceptar.get_attribute('aria-disabled') == 'true'
    ):
        raise ArisNoConfirmado('Aceptar final ARIS no disponible; revisar resultado')
    aceptar.click()
    try:
        dialogo.wait_for(
            state='hidden', timeout=int(cfg.get('aris_timeout_paso_ms', 45000))
        )
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('ARIS no cerró el popup final tras Aceptar') from exc
    LOG.info('ARIS: popup final de %s cerrado mediante Aceptar', estado)


def _cerrar_pestanas_importacion(contexto, principal, anteriores, aris_url):
    """Cerrar solo pestañas ARIS creadas tras lanzar el informe.

    Jamás tocar la pestaña principal, las preexistentes ni sitios ajenos.
    Ante origen o ruta ambiguos se deja la pestaña para revisión manual.
    """
    hostname = urlsplit(aris_url).hostname
    cerradas = 0
    for tab in list(contexto.pages):
        if tab is principal or any(tab is antigua for antigua in anteriores):
            continue
        if tab.is_closed():
            continue
        destino = urlsplit(tab.url)
        if destino.scheme != 'https' or destino.hostname != hostname:
            LOG.warning('ARIS: pestaña nueva ajena/no cargada; no se cierra')
            continue
        if 'repository/' in destino.fragment.lower():
            LOG.warning('ARIS: pestaña nueva de repositorio; no se cierra sin identificar')
            continue
        try:
            tab.close()
            cerradas += 1
            LOG.info('ARIS: pestaña adicional del modelo cerrada')
        except Exception as exc:
            LOG.warning('ARIS: no se pudo cerrar pestaña adicional: %s', type(exc).__name__)
    return cerradas


def procesar_aris(xml: Path, cfg: dict, perfil: Path, al_iniciar=None):
    """Un XML; confirma antes de cualquier acción que inicia la importación.

    al_iniciar sirve para escribir checkpoint ANTES de lanzar el informe.
    """
    xml = validar_xml_aris(xml)
    url = _arise_url(cfg)
    modo = cfg.get('modo_aris', 'inspeccionar')
    if modo not in ('inspeccionar', 'preparar', 'ejecutar'):
        raise ValueError('modo_aris debe ser inspeccionar, preparar o ejecutar')
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(perfil), channel='chrome', headless=False, accept_downloads=True,
            chromium_sandbox=True
        )
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(int(cfg.get('timeout_ms', 120000)))
            page.goto(url, wait_until='domcontentloaded')
            LOG.info('Repositorio ARIS abierto en carpeta configurada')
            if modo == 'inspeccionar':
                LOG.info('Inspección ARIS: sin lanzar informes ni subir XML')
                input('Inspecciona ARIS; Enter para cerrar sin importar... ')
                return {'estado': 'inspeccionado', 'importado': False}
            grupo_aris = seleccionar_informe(page, cfg)
            if modo == 'preparar':
                LOG.info('ARIS: preparación validada; no se lanzó ningún informe')
                input('Informe seleccionado. Inspecciona la pantalla; Enter para cerrar SIN lanzar... ')
                return {'estado': 'preparado_para_lanzar', 'importado': False}
            confirmacion = input(
                f'IMPORTACIÓN REAL en ARIS. Grupo: {grupo_aris}. Archivo: {xml.name}. '
                'Escribe IMPORTAR para lanzar el informe: '
            )
            if confirmacion != 'IMPORTAR':
                LOG.info('Importación cancelada antes de lanzar el informe')
                return {'estado': 'cancelado', 'importado': False}
            if al_iniciar:
                al_iniciar()
            pestanas_anteriores = tuple(ctx.pages)
            page.locator(LAUNCH).click()
            LOG.info('Informe ARIS lanzado')
            etapa_archivo = adjuntar_xml(page, xml, cfg)
            if etapa_archivo == 'opciones':
                _opcion_auto(page, cfg)
                LOG.info('Importación automática confirmada; esperando resultado de ARIS')
            elif etapa_archivo == 'rechazo':
                LOG.warning('No se confirmarán opciones: ARIS ya rechazó el XML')
            else:
                raise ArisNoConfirmado('Resultado inesperado al seleccionar fichero')
            resultado = _resultado(page, cfg)
            cerradas = _cerrar_pestanas_importacion(
                ctx, page, pestanas_anteriores, url
            )
            LOG.info('ARIS: limpieza final completada; pestañas extra cerradas=%s', cerradas)
            if cfg.get('mantener_chrome_abierto', True):
                input('Revisa el resultado en Chrome. Enter para cerrar... ')
            return resultado
        except Exception:
            if cfg.get('mantener_chrome_abierto', True):
                LOG.warning('ARIS: fallo de navegación; Chrome queda abierto para revisar DOM')
                try:
                    input('Revisa la pantalla de ARIS; Enter para cerrar y guardar error... ')
                except (EOFError, KeyboardInterrupt):
                    pass
            raise
        finally:
            ctx.close()
