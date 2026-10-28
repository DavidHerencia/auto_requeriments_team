"""Pausa interactiva por sesión caducada, sin guardar credenciales."""
import logging
from urllib.parse import urlsplit
from playwright.sync_api import TimeoutError as PlaywrightTimeout

LOG = logging.getLogger(__name__)
HOSTS_LOGIN = ('accounts.google.com', 'login.microsoftonline.com', 'auth.openai.com')

def falta_sesion(page, servicio):
    """Solo señales positivas; no inferir caducidad a partir de lentitud."""
    try:
        url = urlsplit(page.url)
        host, path = (url.hostname or '').lower(), url.path.lower()
        if host in HOSTS_LOGIN or host.startswith('login.'):
            return True
        if any(x in path for x in ('/auth/login', '/auth/signin', '/login', '/signin')):
            return True
        if page.locator('input[type="password"]:visible').count():
            return True
        if servicio == 'aris' and page.locator('#Copernicus_GWT_Loginform_holder:visible').count():
            return True
        # El login corporativo también puede mostrarse en un iframe SSO.
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            try:
                frame_host = (urlsplit(frame.url).hostname or '').lower()
                if frame_host in HOSTS_LOGIN or frame_host.startswith('login.'):
                    return True
                if frame.locator('input[type="password"]:visible').count():
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False

def esperar_sesion(page, servicio, url, config, listo):
    """Espera un elemento real de la app. Login requiere confirmación humana.

    Nunca reenvía operaciones previamente iniciadas.
    """
    max_login = int(config.get('max_pausas_login', 3))
    for intento in range(max_login + 1):
        if falta_sesion(page, servicio):
            if intento >= max_login:
                raise RuntimeError(f'{servicio}: sesión no recuperada')
            LOG.warning('%s: autenticación requerida; pausa sin reenviar nada', servicio)
            respuesta = input(
                f'{servicio}: inicia sesión en Chrome. '
                'Cuando vuelvas a ver la aplicación escribe CONTINUAR: '
            )
            if respuesta.strip() != 'CONTINUAR':
                raise KeyboardInterrupt('Inicio de sesión no confirmado')
            page.goto(url, wait_until='domcontentloaded',
                      timeout=int(config.get('timeout_ms', 120000)))
            continue
        try:
            return listo()
        except (PlaywrightTimeout, TimeoutError):
            if falta_sesion(page, servicio):
                continue
            raise
    raise RuntimeError(f'{servicio}: sesión no confirmada')
