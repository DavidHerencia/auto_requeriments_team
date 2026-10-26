import unittest
from unittest.mock import MagicMock

from modules.chatgpt import (
    _respuestas_asistente, TURNO, TURNO_LEGACY, TURNO_LEGACY_2, TURNO_ROLE
)


class TestDeteccionHero(unittest.TestCase):
    def test_contenedor_real_de_mensaje(self):
        page = MagicMock()
        respuesta = _respuestas_asistente(page, {})
        page.locator.assert_called_once_with(
            '[data-chatgpt-search-unit-key$=":assistant"]'
        )
        self.assertIs(respuesta, page.locator.return_value)

    def test_configuraciones_antiguas_no_apuntan_al_h4(self):
        for anterior in (TURNO_LEGACY, TURNO_LEGACY_2, TURNO_ROLE):
            with self.subTest(anterior=anterior):
                page = MagicMock()
                _respuestas_asistente(page, {'gpt_respuesta_selector': anterior})
                page.locator.assert_called_once_with(TURNO)

    def test_custom_si_se_respetan(self):
        page = MagicMock()
        _respuestas_asistente(page, {'gpt_respuesta_selector': '.mi-selector'})
        page.locator.assert_called_once_with('.mi-selector')


if __name__ == '__main__':
    unittest.main()
