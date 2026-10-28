"""Preflight de sesiones antes de iniciar un pipeline: mismo perfil Chrome.

No adjunta XML, no envía prompts, no lanza informes. Comprueba tres
aplicaciones por señales del DOM, con recuperación interactiva del SSO.
"""
import logging

from playwright.sync_api import sync_playwright

from modules.session import navegar_con_sesion, esperar_sesion
from modules.download_xmls import encontrar_frame_puente
from modules.chatgpt import gpt_url, _esperar_editor
from modules.aris import REPORTS_TAB, _abrir_informes, _contexto_actual_informes, _arise_url

LOG = logging.getLogger(__name__)
PASO_MS = 3000


def verificar_sesiones(cfg, perfil):
    """Antes de la extracción, verificar Google, HERO y ARIS en serie."""
    google = cfg.get('apps_script_url', '')
    if not google.startswith('https://'):
        raise ValueError('Configura apps_script_url HTTPS')
    hero = gpt_url(cfg)
    aris = _arise_url(cfg)
    perfil.mkdir(parents=True, exist_ok=True)
    LOG.info('Preflight: comprobando sesiones de Google, GPT HERO y ARIS')
    with sync_playwright() as playwright:
        contexto = playwright.chromium.launch_persistent_context(
            str(perfil), channel='chrome', headless=False,
            accept_downloads=True, chromium_sandbox=True
        )
        try:
            for nombre, url in (('sheets', google), ('chatgpt', hero), ('aris', aris)):
                pagina = contexto.new_page()
                try:
                    navegar_con_sesion(pagina, nombre, url, cfg)
                    if nombre == 'sheets':
                        esperar_sesion(
                            pagina, nombre, url, cfg,
                            lambda: encontrar_frame_puente(pagina, PASO_MS)
                        )
                    elif nombre == 'chatgpt':
                        esperar_sesion(
                            pagina, nombre, url, cfg,
                            lambda: _esperar_editor(pagina, cfg, timeout_ms=PASO_MS)
                        )
                    else:
                        esperar_sesion(
                            pagina, nombre, url, cfg,
                            lambda: pagina.locator(REPORTS_TAB).wait_for(
                                state='visible', timeout=PASO_MS
                            )
                        )
                        # Verificar la carpeta del repositorio sin ejecutar informe.
                        _abrir_informes(pagina, cfg)
                        actual = _contexto_actual_informes(pagina, cfg)
                        esperado = ' '.join(cfg.get('aris_grupo_informes', '').split())
                        if esperado and actual.casefold() != esperado.casefold():
                            raise RuntimeError('Preflight ARIS: carpeta de informes incorrecta')
                    LOG.info('Preflight: %s sesión comprobada y aplicación lista', nombre)
                finally:
                    if not pagina.is_closed():
                        pagina.close()
        finally:
            contexto.close()
    LOG.info('Preflight: tres sesiones comprobadas; iniciando pipeline')
