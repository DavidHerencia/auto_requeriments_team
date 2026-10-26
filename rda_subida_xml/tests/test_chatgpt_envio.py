import unittest
from unittest.mock import MagicMock, patch

from modules.chatgpt import _enviar, _nuevo_turno, _puede_cerrar_respuesta


class TestEnvio(unittest.TestCase):
    @patch('modules.chatgpt._nuevo_turno', return_value='respuesta')
    @patch('modules.chatgpt._esperar_editor')
    def test_enter_unico_sin_esperar_turno_usuario(self, obtener_editor, nuevo_turno):
        page = MagicMock()
        page.locator.return_value.count.return_value = 0
        resultado = _enviar(page, {}, 'prueba')
        self.assertEqual(resultado, 'respuesta')
        obtener_editor.return_value.fill.assert_called_once_with('prueba')
        obtener_editor.return_value.press.assert_called_once_with('Enter', timeout=20000)
        page.wait_for_function.assert_not_called()
        nuevo_turno.assert_called_once_with(page, {}, 0, 0)

    def test_espera_evento_terminal_no_solo_texto(self):
        self.assertFalse(_puede_cerrar_respuesta(False, True, True, False))
        self.assertFalse(_puede_cerrar_respuesta(False, False, False, True))
        self.assertFalse(_puede_cerrar_respuesta(False, False, True, False))
        self.assertTrue(_puede_cerrar_respuesta(True, False, False, False))
        self.assertTrue(_puede_cerrar_respuesta(False, False, True, True))

    @patch('modules.chatgpt._marcadores_generacion')
    def test_turno_asistente_confirma_envio_y_espera_fin(self, marcadores):
        page = MagicMock()
        page.locator.return_value.count.return_value = 1
        nodo = page.locator.return_value.nth.return_value
        nodo.inner_text.return_value = '<definitions/>'
        nodo.locator.return_value.all_text_contents.return_value = ['<definitions/>']
        marcadores.side_effect = [
            (False, True, False),
            (False, True, False),
            (True, False, False),
            (True, False, False),
        ]
        resultado = _nuevo_turno(page, {'timeout_respuesta_ms': 30000}, 0, 0)
        self.assertIs(resultado, nodo)
        self.assertEqual(marcadores.call_count, 4)
        self.assertGreaterEqual(page.wait_for_timeout.call_count, 3)


if __name__ == '__main__':
    unittest.main()
