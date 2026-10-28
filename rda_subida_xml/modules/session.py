"""Pausa interactiva por sesión caducada, sin guardar credenciales.

Después de SSO el navegador se redirige solo a la aplicación. Forzar goto()
tras CONTINUAR interrumpe ese retorno y produce el error de navegación
observado con Apps Script / IdP BBVA.
"""
import logging
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeout

LOG = logging.getLogger(__name__)
HOSTS_LOGIN = ('accounts.google.com', 'login.microsoftonline.com', 'auth.openai.com')


def _host_autenticacion(host):
    return (host in HOSTS_LOGIN or host.startswith('login.')
            or host.startswith('idp.'))


def falta_sesion(page, servicio):
    """Señales positivas de autenticación; la lentitud no implica logout."""
    try:
        url = urlsplit(page.url)
        host, path = (url.hostname or '').lower(), url.path.lower()
        if _host_autenticacion(host):
            return True
        if any(x in path for x in ('/auth/login', '/auth/signin', '/login', '/signin')):
            return True
        if page.locator('input[type="password"]:visible').count():
            return True
        if (servicio == 'aris'
                and page.locator('#Copernicus_GWT_Loginform_holder:visible').count()):
            return True
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                frame_host = (urlsplit(frame.url).hostname or '').lower()
                if _host_autenticacion(frame_host):
                    return True
                if frame.locator('input[type="password"]:visible').count():
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def esperar_sesion(page, servicio, url, config, listo):
    """Esperar evidencia DOM de la aplicación y pausar ante una sesión expirada.

    No navegar de nuevo tras el login: el proveedor SSO está redirigiendo
    y un nuevo page.goto() interrumpe esa navegación. Tampoco reenviar XML,
    prompts ni informes. 'listo' comprueba el elemento real de cada etapa,
    con timeout máximo determinado por el módulo llamador.
    """
    max_login = int(config.get('max_pausas_login', 3))
    if max_login < 0:
        raise ValueError('max_pausas_login no puede ser negativo')
    pausas = 0
    while True:
        if falta_sesion(page, servicio):
            if pausas >= max_login:
                raise RuntimeError(f'{servicio}: sesión no recuperada tras {pausas} pausas')
            pausas += 1
            LOG.warning('%s: autenticación requerida (%s/%s); pausa sin reenviar',
                        servicio, pausas, max_login)
            respuesta = input(
                f'{servicio}: completa el login en Chrome y deja que regrese '
                'automáticamente a la aplicación. Cuando esté lista, '
                'escribe CONTINUAR: '
            )
            if respuesta.strip() != 'CONTINUAR':
                raise KeyboardInterrupt('Inicio de sesión no confirmado')
            LOG.info('%s: esperando aplicación lista tras SSO, sin otra navegación',
                     servicio)
        try:
            return listo()
        except (PlaywrightTimeout, TimeoutError) as exc:
            if falta_sesion(page, servicio):
                LOG.warning('%s: la autenticación continúa; esperar confirmación',
                            servicio)
                continue
            raise RuntimeError(
                f'{servicio}: la aplicación no llegó a estado listo; '
                'revisar navegador y logs, sin reenviar acciones'
            ) from exc
