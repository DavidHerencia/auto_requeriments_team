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
REPORT_SELECTOR = 'div.reportActionPanel input[role="combobox"]'
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


def seleccionar_informe(page, cfg):
    """Usa el informe EXACTO; no lo escribe ni elige una coincidencia parcial."""
    report = cfg.get('aris_report_name', REPORT)
    combo = page.locator(REPORT_SELECTOR)
    # El usuario puede haber dejado abierta la pestaña Informes en la sesión.
    if not (combo.count() and combo.first.is_visible()):
        page.locator(REPORTS_TAB).click()
    combo.wait_for(state='visible')
    if combo.count() != 1:
        raise ArisNoConfirmado('No se encontró un único selector de informes')

    # El campo es un combobox GWT. Seleccionar con click, NO con fill().
    combo.click()
    _una_visible(page.get_by_text(report, exact=True), 'Opción de informe').click()
    actual = combo.input_value().strip()
    if actual != report:
        raise ArisNoConfirmado('El campo no confirmó el informe exacto')
    LOG.info('Informe ARIS seleccionado: %s', report)


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


def adjuntar_xml(page, ruta):
    input_file = page.locator(FILE_INPUT)
    input_file.wait_for(state='attached')
    if input_file.count() != 1:
        raise ArisNoConfirmado('No se encontró un único input de archivo XML')
    dialogo = _dialogo_con(page, FILE_INPUT)
    if dialogo.count() != 1:
        raise ArisNoConfirmado('No se encontró el modal Seleccionar fichero XML')
    input_file.set_input_files(str(ruta))
    nombres = input_file.evaluate('el => Array.from(el.files || []).map(f => f.name)')
    if nombres != [ruta.name]:
        raise ArisNoConfirmado('El selector de archivos no confirmó el XML solicitado')
    LOG.info('Archivo seleccionado para ARIS (sin copiar su contenido): %s', ruta.name)
    _aceptar(dialogo, 'Aceptar archivo XML')


def _opcion_auto(page, cfg):
    page.get_by_text('Opciones de importación', exact=True).wait_for(state='visible')
    modal = _una_visible(page.locator(DIALOG).filter(
        has=page.get_by_text('Opciones de importación', exact=True)
    ), 'Modal de opciones de importación')
    expected = cfg.get('aris_tipo_importacion', 'Importación automática')
    # Algunos dropdowns GWT presentan una etiqueta visible, otros un
    # input con value. Ambos exigen igualdad exacta con la opción autorizada.
    seleccion_confirmada = False
    for control in modal.locator('input, select').all():
        if not control.is_visible():
            continue
        try:
            if control.input_value().strip() == expected:
                seleccion_confirmada = True
        except Exception:
            continue
    if not seleccion_confirmada:
        for opcion in modal.get_by_text(expected, exact=True).all():
            if opcion.is_visible():
                seleccion_confirmada = True
                break
    if not seleccion_confirmada:
        raise ArisNoConfirmado(
            'No se pudo comprobar Importación automática; inspeccionar dropdown real'
        )
    LOG.info('Tipo de importación confirmado: %s', expected)
    _aceptar(modal, 'Aceptar opciones de importación')


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
    return {'estado': estado, 'importado': None if estado == 'informe_completo' else False,
            'mensaje': FILE_ERROR if estado == 'xml_rechazado' else REPORT_DONE}


def procesar_aris(xml: Path, cfg: dict, perfil: Path, al_iniciar=None):
    """Un XML; confirma antes de cualquier acción que inicia la importación.

    al_iniciar sirve para escribir checkpoint ANTES de lanzar el informe.
    """
    xml = validar_xml_aris(xml)
    url = _arise_url(cfg)
    modo = cfg.get('modo_aris', 'inspeccionar')
    if modo not in ('inspeccionar', 'ejecutar'):
        raise ValueError('modo_aris debe ser inspeccionar o ejecutar')
    perfil.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(perfil), channel='chrome', headless=False, accept_downloads=True
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
            seleccionar_informe(page, cfg)
            confirmacion = input(
                f'IMPORTACIÓN REAL en ARIS. Archivo: {xml.name}. '
                'Escribe IMPORTAR para lanzar el informe: '
            )
            if confirmacion != 'IMPORTAR':
                LOG.info('Importación cancelada antes de lanzar el informe')
                return {'estado': 'cancelado', 'importado': False}
            if al_iniciar:
                al_iniciar()
            page.locator(LAUNCH).click()
            LOG.info('Informe ARIS lanzado')
            adjuntar_xml(page, xml)
            _opcion_auto(page, cfg)
            LOG.info('Importación automática confirmada; esperando resultado de ARIS')
            resultado = _resultado(page, cfg)
            if cfg.get('mantener_chrome_abierto', True):
                input('Revisa el resultado en Chrome. Enter para cerrar... ')
            return resultado
        finally:
            ctx.close()
