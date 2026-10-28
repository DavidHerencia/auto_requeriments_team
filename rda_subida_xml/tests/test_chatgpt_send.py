"""Regresiones de envío en HERO: un click, no Enter ni duplicados."""
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from modules.chatgpt import (
    _enviar, INICIAL, ENVIAR, ResultadoIncierto, TURNO
)


class TestEnvioHero(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.editor = MagicMock()
        self.boton = MagicMock()
        self.boton.count.return_value = 1
        self.respuestas = MagicMock()
        self.respuestas.count.return_value = 0
        self.page.locator.side_effect = lambda selector: (
            self.boton if selector == ENVIAR else MagicMock(count=MagicMock(return_value=0))
        )
        self.page.wait_for_function.return_value.json_value.return_value = 'editor_vaciado'

    def _enviar(self, **kwargs):
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno', return_value='RESPUESTA') as nuevo:
            resultado = _enviar(self.page, {}, INICIAL, **kwargs)
            return resultado, nuevo

    def test_prompt_con_skill_click_envio_sin_enter(self):
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.assertIn('@BPMN ARIS XML Repair', INICIAL)
        self.editor.fill.assert_called_once_with(INICIAL)
        self.editor.press.assert_not_called()
        self.boton.click.assert_called_once_with(timeout=30000)
        self.page.wait_for_function.assert_called_once()
        nuevo.assert_called_once_with(self.page, {}, 0, 0)

    def test_el_evento_de_confirmacion_usa_editor_y_respuesta(self):
        _, _ = self._enviar()
        argumentos = self.page.wait_for_function.call_args.kwargs
        self.assertEqual(argumentos['arg']['responseSelector'], TURNO)
        self.assertEqual(argumentos['arg']['previous'], 0)
        self.assertEqual(argumentos['timeout'], 30000)

    def test_click_sin_confirmacion_no_hace_segundo_envio(self):
        self.page.wait_for_function.side_effect = PlaywrightTimeout('sin transición')
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno') as nuevo:
            with self.assertRaisesRegex(ResultadoIncierto, 'no se reenviará'):
                _enviar(self.page, {}, INICIAL)
            self.boton.click.assert_called_once()
            self.editor.press.assert_not_called()
            nuevo.assert_not_called()

    def test_boton_no_accionable_no_intenta_enter(self):
        self.boton.click.side_effect = PlaywrightTimeout('deshabilitado')
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno') as nuevo:
            with self.assertRaisesRegex(ResultadoIncierto, 'con certeza'):
                _enviar(self.page, {}, INICIAL)
            self.boton.click.assert_called_once()
            self.editor.press.assert_not_called()
            nuevo.assert_not_called()

    def test_boton_ambiguo_falla_sin_click(self):
        self.boton.count.return_value = 2
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas):
            with self.assertRaisesRegex(ResultadoIncierto, 'ambiguo'):
                _enviar(self.page, {}, INICIAL)
        self.boton.click.assert_not_called()


if __name__ == '__main__':
    unittest.main()
