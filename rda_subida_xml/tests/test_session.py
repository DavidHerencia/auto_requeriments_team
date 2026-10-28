"""Regresión de autenticación: sin navegar dos veces durante SSO."""
import unittest
from unittest.mock import MagicMock, patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from modules.session import esperar_sesion, falta_sesion


class TestSession(unittest.TestCase):
    def pagina(self, url):
        page = MagicMock()
        page.url = url
        page.frames = []
        page.locator.return_value.count.return_value = 0
        return page

    def test_idp_corporativo_se_reconoce_como_login(self):
        page = self.pagina('https://idp.live.global.platform.bbva.com/idp/profile/SAML2/Redirect/SSO')
        self.assertTrue(falta_sesion(page, 'sheets'))

    def test_confirmar_sso_no_interrumpe_redirect_con_goto(self):
        page = self.pagina('https://idp.live.global.platform.bbva.com/idp/profile/SAML2/Redirect/SSO')
        terminado = object()
        def listo():
            page.url = 'https://script.google.com/a/macros/bbva.com/s/ejemplo/exec'
            return terminado
        with patch('builtins.input', return_value='CONTINUAR') as pedir:
            resultado = esperar_sesion(page, 'sheets', page.url, {}, listo)
        self.assertIs(resultado, terminado)
        pedir.assert_called_once()
        page.goto.assert_not_called()

    def test_esperar_sso_lento_permite_nueva_pausa_no_navegacion(self):
        page = self.pagina('https://idp.live.global.platform.bbva.com/idp/profile/SAML2/Redirect/SSO')
        intentos = iter([PlaywrightTimeout('SSO sigue redirigiendo'), 'listo'])
        def listo():
            return next(intentos)
        # Simular la primera espera con timeout y la segunda con resultado.
        def espera():
            respuesta = listo()
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta
        with patch('builtins.input', return_value='CONTINUAR') as pedir:
            self.assertEqual(esperar_sesion(page, 'sheets', page.url, {}, espera), 'listo')
        self.assertEqual(pedir.call_count, 2)
        page.goto.assert_not_called()

    def test_sesion_existente_no_pide_login(self):
        page = self.pagina('https://chatgpt.com/')
        with patch('builtins.input') as pedir:
            self.assertEqual(
                esperar_sesion(page, 'chatgpt', page.url, {}, lambda: 'listo'),
                'listo'
            )
        pedir.assert_not_called()

    def test_sin_confirmacion_no_navega(self):
        page = self.pagina('https://accounts.google.com/')
        with patch('builtins.input', return_value='CANCELAR'):
            with self.assertRaises(KeyboardInterrupt):
                esperar_sesion(page, 'sheets', page.url, {}, lambda: 'listo')
        page.goto.assert_not_called()


if __name__ == '__main__':
    unittest.main()
