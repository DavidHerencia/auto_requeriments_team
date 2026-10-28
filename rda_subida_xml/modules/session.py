"""Pausa interactiva por sesión caducada, sin guardar credenciales.

Después de SSO el navegador se redirige solo a la aplicación. Forzar goto()
tras CONTINUAR interrumpe ese retorno y produce el error de navegación
observado con Apps Script / IdP BBVA.
"""
import logging
import time
from urllib.parse import urlsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeout, Error as PlaywrightError

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


def navegar_con_sesion(page, servicio, url, config):
    """Navegación inicial, tolerando únicamente una redirección SSO observada.

    La autenticación se realizará en esperar_sesion, sin recargar ni repetir
    acciones durante un redirect en curso.
    """
    try:
        page.goto(url, wait_until='domcontentloaded',
                  timeout=int(config.get('timeout_ms', 120000)))
    except PlaywrightError as exc:
        if ('interrupted by another navigation' not in str(exc)
                or not falta_sesion(page, servicio)):
            raise
        LOG.warning('%s: navegación interrumpida por redirección de autenticación; '
                    'esperando SSO sin recargar', servicio)


def esperar_sesion(page, servicio, url, config, listo):
    """Comprobar estado real del DOM; observar login entre sondeos cortos.

    `listo` debe hacer una espera pequeña (p.ej. 3 segundos) y lanzar
    PlaywrightTimeout/TimeoutError si la aplicación sigue cargando. Así
    podemos detectar IDP de inmediato, no al cabo de 120 segundos.
    """
    max_login = int(config.get('max_pausas_login', 3))
    timeout_ms = int(config.get('timeout_sesion_ms',
                                config.get('timeout_ms', 120000)))
    if max_login < 0 or timeout_ms <= 0:
        raise ValueError('Configuración de sesión inválida')
    pausas, ultimo_log = 0, 0.0
    esperando_retorno_sso = False
    limite = time.monotonic() + timeout_ms / 1000
    while True:
        autenticando = falta_sesion(page, servicio)
        if not autenticando:
            esperando_retorno_sso = False
        if autenticando and not esperando_retorno_sso:
            if pausas >= max_login:
                raise RuntimeError(f'{servicio}: sesión no recuperada tras {pausas} pausas')
            pausas += 1
            LOG.warning('%s: REDIRECCIÓN A LOGIN/SSO detectada (%s/%s). '
                        'Se necesita intervención del usuario.',
                        servicio, pausas, max_login)
            respuesta = input(
                f'{servicio.upper()}: es necesario iniciar sesión en Chrome. '
                'Completa el login corporativo y deja que el navegador '
                'regrese automáticamente a la aplicación. '
                'Cuando esté lista, escribe CONTINUAR: '
            )
            if respuesta.strip() != 'CONTINUAR':
                raise KeyboardInterrupt('Inicio de sesión no confirmado')
            LOG.info('%s: verificando sesión tras SSO, sin nueva navegación', servicio)
            esperando_retorno_sso = True
            limite = time.monotonic() + timeout_ms / 1000
        try:
            return listo()
        except (PlaywrightTimeout, TimeoutError) as exc:
            # Continuar observando la URL y el DOM sin bloquear en un wait largo.
            ahora = time.monotonic()
            if ahora >= limite and falta_sesion(page, servicio):
                # Si el SSO sigue abierto, solicitar otra intervención; no
                # repetir el prompt cada 3 segundos mientras redirige.
                esperando_retorno_sso = False
                continue
            if ahora >= limite:
                raise RuntimeError(
                    f'{servicio}: la aplicación no llegó a estado listo '
                    'tras verificar la sesión; revisar Chrome sin reenviar operaciones'
                ) from exc
            if ahora - ultimo_log > 15:
                LOG.info('%s: esperando señal de aplicación lista', servicio)
                ultimo_log = ahora
