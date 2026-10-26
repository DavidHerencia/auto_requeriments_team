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
    """Obtiene el contexto visible del informe; nunca asume carpeta predeterminada.

    En GWT puede existir un .label oculto y el texto tarda en actualizarse.
    Solo se usa el panel visible y se registran los valores observados.
    """
    panel = page.locator('.sidebar.reportSidebar .reportContextPanel')
    try:
        panel.wait_for(state='visible', timeout=int(cfg.get('aris_timeout_seleccion_ms', 20000)))
        page.wait_for_function(
            """() => [...document.querySelectorAll('.sidebar.reportSidebar .reportContextPanel .label')]
                .some(el => {
                    const box = el.getBoundingClientRect();
                    return box.width > 0 && box.height > 0 && !!el.textContent.trim();
                })""",
            timeout=int(cfg.get('aris_timeout_seleccion_ms', 20000))
        )
    except PlaywrightTimeout as exc:
        raise ArisNoConfirmado('No se pudo leer el grupo real del panel Informes') from exc

    nombres = [' '.join(x.inner_text().split()) for x in panel.locator('.label').all() if x.is_visible()]
    nombres = [x for x in nombres if x]
    LOG.info('ARIS: contexto real del panel Informes: %s', nombres)
    if len(nombres) != 1:
        raise ArisNoConfirmado(f'Contexto Informes ambiguo: {len(nombres)} etiquetas visibles')
    actual = nombres[0]

    # Comprobación estricta SOLO cuando el operador fija el grupo localmente.
    # En preparar registramos la discrepancia, para poder avanzar al desplegable
    # sin realizar ninguna importación; en ejecutar nunca ignoramos un desacuerdo.
    esperado = ' '.join((cfg.get('aris_grupo_informes') or '').split())
    if esperado and actual.casefold() != esperado.casefold():
        if cfg.get('modo_aris') == 'ejecutar':
            raise ArisNoConfirmado(
                f'Carpeta incorrecta para importar: actual={actual!r}, configurada={esperado!r}'
            )
        LOG.warning('ARIS: carpeta esperada=%r, observada=%r; '
                    'modo preparar: sin lanzar informe', esperado, actual)
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
    if modo not in ('inspeccionar', 'preparar', 'ejecutar'):
        raise ValueError('modo_aris debe ser inspeccionar, preparar o ejecutar')
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
            page.locator(LAUNCH).click()
            LOG.info('Informe ARIS lanzado')
            adjuntar_xml(page, xml)
            _opcion_auto(page, cfg)
            LOG.info('Importación automática confirmada; esperando resultado de ARIS')
            resultado = _resultado(page, cfg)
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
