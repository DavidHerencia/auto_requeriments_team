"""Regresiones de recuperación tras archivo de HERO sin descarga."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from modules.chatgpt import (
    INICIAL, RECUPERAR, SalidaSinXml, _leer_resultado, _procesar_uno
)


class TestRecuperacionDescarga(unittest.TestCase):
    def test_prompt_inicial_se_conserva_y_recuperacion_exige_texto(self):
        self.assertIn('XML listo para descargar', INICIAL)
        self.assertTrue(
            RECUPERAR.startswith(
                'Dame solamente el XML listo para copiar y pegar '
                '(NO para descargar) sin nada mas o, '
            )
        )
        self.assertNotIn('XML listo para descargar', RECUPERAR)

    def test_archivo_ofrecido_sin_evento_descarga_activa_recuperacion(self):
        page = MagicMock()
        page.expect_download.return_value.__exit__.side_effect = (
            PlaywrightTimeout('no hubo descarga')
        )
        cuerpo = MagicMock()
        cuerpo.inner_text.return_value = 'El XML está listo. Descargar XML original'
        contenedor = MagicMock()
        contenedor.count.return_value = 1
        contenedor.first = cuerpo
        boton = MagicMock()
        boton.count.return_value = 1
        nodo = MagicMock()
        nodo.locator.side_effect = lambda selector: (
            contenedor if selector == '[data-markdown-text-style="assistant-message"]'
            else boton
        )
        with tempfile.TemporaryDirectory() as d:
            temporal = Path(d) / 'xml.xml'
            with self.assertRaisesRegex(SalidaSinXml, 'no se pudo descargar'):
                _leer_resultado(page, nodo, {}, temporal)
            self.assertFalse(temporal.exists())
        page.expect_download.assert_called_once_with(timeout=30000)
        boton.first.click.assert_called_once()

    def test_si_falla_descarga_recupera_xml_con_segundo_prompt_sin_adjunto(self):
        page = MagicMock()
        original = Path('ejemplo.xml')
        temporal = Path('temporal.xml')
        xml = '<definitions/>'
        with patch('modules.chatgpt._enviar', side_effect=['turno1', 'turno2']) as enviar, \
             patch('modules.chatgpt._leer_resultado',
                   side_effect=[SalidaSinXml('no se pudo descargar'), xml]) as leer:
            resultado = _procesar_uno(page, {}, original, temporal)
        self.assertEqual(resultado, xml)
        self.assertEqual(enviar.call_args_list, [
            call(page, {}, INICIAL, original),
            call(page, {}, RECUPERAR),
        ])
        self.assertEqual(leer.call_count, 2)

    def test_respuesta_textual_sin_xml_conserva_recuperacion_unica(self):
        page = MagicMock()
        original = Path('ejemplo.xml')
        temporal = Path('temporal.xml')
        with patch('modules.chatgpt._enviar', side_effect=['turno1', 'turno2']) as enviar, \
             patch('modules.chatgpt._leer_resultado',
                   side_effect=[SalidaSinXml('texto sin XML'), '<definitions/>']):
            self.assertEqual(
                _procesar_uno(page, {}, original, temporal), '<definitions/>'
            )
        self.assertEqual(enviar.call_count, 2)

    def test_xml_exitoso_no_dispara_recuperacion(self):
        page = MagicMock()
        original = Path('ejemplo.xml')
        temporal = Path('temporal.xml')
        with patch('modules.chatgpt._enviar', return_value='turno1') as enviar, \
             patch('modules.chatgpt._leer_resultado', return_value='<definitions/>'):
            resultado = _procesar_uno(page, {}, original, temporal)
        self.assertEqual(resultado, '<definitions/>')
        enviar.assert_called_once_with(page, {}, INICIAL, original)


if __name__ == '__main__':
    unittest.main()
