import unittest
from unittest.mock import MagicMock, patch
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from modules.chatgpt import _enviar, ResultadoIncierto


class TestEnvio(unittest.TestCase):
    @patch('modules.chatgpt._nuevo_turno', return_value='respuesta')
    @patch('modules.chatgpt._esperar_editor')
    def test_envio_unico_con_enter_y_confirmacion(self, obtener_editor, nuevo_turno):
        editor = obtener_editor.return_value
        pagina = MagicMock()
        pagina.locator.return_value.count.side_effect = [0, 0]
        respuesta = _enviar(pagina, {}, 'prueba')
        self.assertEqual(respuesta, 'respuesta')
        editor.fill.assert_called_once_with('prueba')
        editor.press.assert_called_once_with('Enter', timeout=20000)
        pagina.wait_for_function.assert_called_once()
        nuevo_turno.assert_called_once_with(pagina, {}, 0, 0)

    @patch('modules.chatgpt._nuevo_turno')
    @patch('modules.chatgpt._esperar_editor')
    def test_timeout_envio_no_repite_enter(self, obtener_editor, nuevo_turno):
        pagina = MagicMock()
        pagina.locator.return_value.count.side_effect = [0, 0]
        pagina.wait_for_function.side_effect = PlaywrightTimeout('Sin nuevo turno')
        with self.assertRaises(ResultadoIncierto):
            _enviar(pagina, {}, 'prueba')
        obtener_editor.return_value.press.assert_called_once_with('Enter', timeout=20000)
        nuevo_turno.assert_not_called()


if __name__ == '__main__':
    unittest.main()
