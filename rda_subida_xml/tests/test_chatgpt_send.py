"""Regresiones de envío en HERO: Enter primero, click seguro de respaldo."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from modules.chatgpt import (
    _enviar, INICIAL, ENVIAR, ResultadoIncierto, EnvioNoRealizado, TURNO, procesar_registros
)


class TestEnvioHero(unittest.TestCase):
    def setUp(self):
        self.page = MagicMock()
        self.editor = MagicMock()
        self.boton = MagicMock()
        self.boton.count.return_value = 1
        self.boton.is_visible.return_value = True
        self.boton.is_enabled.return_value = True
        self.respuestas = MagicMock()
        self.respuestas.count.return_value = 0
        self.page.locator.side_effect = lambda selector: (
            self.boton if selector == ENVIAR
            else MagicMock(count=MagicMock(return_value=0))
        )
        self.page.wait_for_function.return_value.json_value.return_value = 'editor_vaciado'

    def _enviar(self, **kwargs):
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno', return_value='RESPUESTA') as nuevo:
            resultado = _enviar(self.page, {}, INICIAL, **kwargs)
            return resultado, nuevo

    def test_enter_funciona_y_no_hace_click(self):
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.assertIn('@BPMN ARIS XML Repair', INICIAL)
        self.editor.fill.assert_called_once_with(INICIAL)
        self.editor.press.assert_called_once_with('Enter', timeout=60000)
        self.boton.click.assert_not_called()
        self.page.evaluate.assert_not_called()
        self.page.wait_for_function.assert_called_once()
        nuevo.assert_called_once_with(self.page, {}, 0, 0)

    def test_el_evento_tras_enter_usa_editor_y_respuesta(self):
        self._enviar()
        argumentos = self.page.wait_for_function.call_args.kwargs
        self.assertEqual(argumentos['arg']['responseSelector'], TURNO)
        self.assertEqual(argumentos['arg']['previousResponses'], 0)
        self.assertEqual(argumentos['timeout'], 20000)

    def test_sin_evento_enter_click_unico_si_editor_sigue_pendiente(self):
        self.page.wait_for_function.side_effect = [
            PlaywrightTimeout('Enter no envió'),
            MagicMock(json_value=MagicMock(return_value='respuesta_nueva'))
        ]
        self.page.evaluate.return_value = {
            'pendiente': True, 'motivo': 'mensaje_sigue_en_editor'
        }
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.editor.press.assert_called_once_with('Enter', timeout=60000)
        self.boton.click.assert_called_once_with(timeout=60000)
        self.assertEqual(self.page.wait_for_function.call_count, 2)
        nuevo.assert_called_once()

    def test_enter_tardio_tiene_turno_y_no_hace_click(self):
        self.page.wait_for_function.side_effect = PlaywrightTimeout('sin evento')
        self.page.evaluate.return_value = {
            'pendiente': False, 'motivo': 'turno_nuevo'
        }
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.boton.click.assert_not_called()
        nuevo.assert_called_once()

    def test_enter_con_menu_mention_escape_y_click_si_habilitado(self):
        self.page.wait_for_function.side_effect = [
            PlaywrightTimeout('sin evento Enter'),
            MagicMock(json_value=MagicMock(return_value='respuesta_nueva'))
        ]
        self.page.evaluate.side_effect = [
            {'pendiente': False, 'motivo': 'boton_no_habilitado',
             'caracteresPendientes': len(INICIAL)},
            {'pendiente': True, 'motivo': 'mensaje_sigue_en_editor',
             'caracteresPendientes': len(INICIAL)}
        ]
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.editor.press.assert_any_call('Escape', timeout=10000)
        self.boton.click.assert_called_once_with(timeout=60000)
        nuevo.assert_called_once()

    def test_enter_sin_evento_boton_inhabilitado_se_reporta(self):
        self.page.wait_for_function.side_effect = PlaywrightTimeout('sin evento')
        self.page.evaluate.side_effect = [
            {'pendiente': False, 'motivo': 'boton_no_habilitado',
             'caracteresPendientes': len(INICIAL)},
            {'pendiente': False, 'motivo': 'boton_no_habilitado',
             'caracteresPendientes': len(INICIAL)},
            {'pendiente': False, 'motivo': 'boton_no_habilitado',
             'caracteresPendientes': len(INICIAL)}
        ]
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno') as nuevo:
            with self.assertRaisesRegex(EnvioNoRealizado, 'ni Enter ni botón'):
                _enviar(self.page, {}, INICIAL)
        self.editor.press.assert_any_call('Escape', timeout=10000)
        self.editor.press.assert_any_call('Enter', timeout=60000)
        self.boton.click.assert_not_called()
        nuevo.assert_not_called()

    def test_enter_sin_evento_y_boton_ambiguo_no_click(self):
        self.page.wait_for_function.side_effect = PlaywrightTimeout('sin evento')
        self.page.evaluate.return_value = {'pendiente': True}
        self.boton.count.return_value = 2
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas):
            with self.assertRaisesRegex(EnvioNoRealizado, 'no accionable'):
                _enviar(self.page, {}, INICIAL)
        self.boton.click.assert_not_called()

    def test_click_respaldo_sin_confirmacion_no_hace_tercer_intento(self):
        self.page.wait_for_function.side_effect = PlaywrightTimeout('sin señal')
        self.page.evaluate.return_value = {'pendiente': True}
        with patch('modules.chatgpt._esperar_editor', return_value=self.editor), \
             patch('modules.chatgpt._respuestas_asistente', return_value=self.respuestas), \
             patch('modules.chatgpt._nuevo_turno') as nuevo:
            with self.assertRaisesRegex(ResultadoIncierto, 'no se duplicará'):
                _enviar(self.page, {}, INICIAL)
            self.editor.press.assert_called_once()
            self.boton.click.assert_called_once()
            nuevo.assert_not_called()

    def test_si_enter_lanza_excepcion_se_inspecciona_antes_del_click(self):
        self.editor.press.side_effect = PlaywrightTimeout('evento Enter incierto')
        self.page.evaluate.return_value = {
            'pendiente': True, 'motivo': 'mensaje_sigue_en_editor',
            'caracteresPendientes': len(INICIAL)
        }
        resultado, nuevo = self._enviar()
        self.assertEqual(resultado, 'RESPUESTA')
        self.editor.press.assert_called_once()
        self.boton.click.assert_called_once_with(timeout=60000)
        nuevo.assert_called_once()

    def test_envio_incierto_no_detiene_lote_y_guarda_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'data'
            archivo = base / 'exportaciones' / 'registros_prueba.json'
            archivo.parent.mkdir(parents=True)
            archivo.write_text(json.dumps({
                'version': 1,
                'registros': [
                    {'id_solicitud': f'S{i}', 'id_n5': f'N{i}',
                     'nombre_proceso': f'Prueba {i}', 'xml': '<definitions/>'}
                    for i in (1, 2)
                ]
            }), encoding='utf-8')
            ctx = MagicMock()
            ctx.pages = [MagicMock()]
            with patch('modules.chatgpt.CHROME_PROFILE', base / 'perfil'), \
                 patch('modules.chatgpt.sync_playwright') as playwright, \
                 patch('modules.chatgpt.gpt_url', return_value='https://chatgpt.com/g/hero'), \
                 patch('modules.chatgpt.registrar_flags_chrome'), \
                 patch('modules.chatgpt.instalar_diagnostico'), \
                 patch('modules.chatgpt.esperar_sesion'), \
                 patch('modules.chatgpt._procesar_uno',
                       side_effect=ResultadoIncierto('interfaz ambigua')) as procesar:
                playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = ctx
                procesar_registros(
                    archivo, {'mantener_chrome_abierto': False}, base,
                    limite=None, confirmar=False
                )
            self.assertEqual(procesar.call_count, 2)
            ruta = base / 'exportaciones' / 'procesados_registros_prueba.json'
            registros = json.loads(ruta.read_text(encoding='utf-8'))['registros']
            self.assertEqual(registros[0]['estado'], 'requiere_revision')
            self.assertEqual(registros[1]['estado'], 'requiere_revision')

    def test_ctrl_c_en_gpt_detiene_pipeline_sin_seguir_a_aris(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'data'
            origen = base / 'exportaciones' / 'registros_prueba.json'
            origen.parent.mkdir(parents=True)
            origen.write_text(json.dumps({
                'version': 1,
                'registros': [{
                    'id_solicitud': 'S1', 'id_n5': 'N1',
                    'nombre_proceso': 'Prueba', 'xml': '<definitions/>'
                }]
            }), encoding='utf-8')
            ctx = MagicMock()
            ctx.pages = [MagicMock()]
            with patch('modules.chatgpt.CHROME_PROFILE', base / 'perfil'), \
                 patch('modules.chatgpt.sync_playwright') as playwright, \
                 patch('modules.chatgpt.gpt_url', return_value='https://chatgpt.com/g/hero'), \
                 patch('modules.chatgpt.registrar_flags_chrome'), \
                 patch('modules.chatgpt.instalar_diagnostico'), \
                 patch('modules.chatgpt._esperar_editor'), \
                 patch('modules.chatgpt._procesar_uno', side_effect=KeyboardInterrupt):
                playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = ctx
                with self.assertRaisesRegex(KeyboardInterrupt, 'pipeline detenido'):
                    procesar_registros(origen, {}, base, limite=None, confirmar=False)
            checkpoint = base / 'exportaciones' / 'procesados_registros_prueba.json'
            estado = json.loads(checkpoint.read_text(encoding='utf-8'))['registros'][0]['estado']
            self.assertEqual(estado, 'requiere_revision')



if __name__ == '__main__':
    unittest.main()
