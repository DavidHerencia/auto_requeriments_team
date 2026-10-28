"""Regresión de autenticación: sin navegar dos veces durante SSO."""
import unittest
from unittest.mock import MagicMock, patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout, Error as PlaywrightError

from modules.session import esperar_sesion, falta_sesion, navegar_con_sesion


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
        # Tras CONTINUAR no debe pedirlo otra vez cada sondeo corto.
        self.assertEqual(pedir.call_count, 1)
        page.goto.assert_not_called()

    def test_detecta_sso_durante_sondeo_sin_esperar_timeout_largo(self):
        page = self.pagina('https://script.google.com/macros/s/ejemplo/exec')
        intentos = [0]
        def listo():
            intentos[0] += 1
            if intentos[0] == 1:
                page.url = (
                    'https://idp.live.global.platform.bbva.com/idp/profile/SAML2/Redirect/SSO'
                )
                raise PlaywrightTimeout('puente todavía no disponible')
            page.url = 'https://script.google.com/macros/s/ejemplo/exec'
            return 'puente_listo'
        with patch('builtins.input', return_value='CONTINUAR') as pedir:
            result = esperar_sesion(page, 'sheets', page.url, {}, listo)
        self.assertEqual(result, 'puente_listo')
        self.assertEqual(intentos[0], 2)
        pedir.assert_called_once()
        page.goto.assert_not_called()

    def test_navegacion_interrumpida_por_sso_no_recarga(self):
        page = self.pagina(
            'https://idp.live.global.platform.bbva.com/idp/profile/SAML2/Redirect/SSO'
        )
        page.goto.side_effect = PlaywrightError(
            'Navigation to Apps Script is interrupted by another navigation'
        )
        navegar_con_sesion(page, 'aris', 'https://aris.example.org/repository/', {})
        page.goto.assert_called_once()

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
