"""El preflight abre el perfil una sola vez y no ejecuta el informe."""
import unittest
from unittest.mock import MagicMock, patch

from modules.preflight import verificar_sesiones


class TestPreflight(unittest.TestCase):
    def test_tres_sesiones_antes_del_lote_mismo_perfil(self):
        cfg = {
            'apps_script_url': 'https://script.google.com/macros/s/ejemplo/exec',
            'hero_gpt_url': 'https://chatgpt.com/g/ejemplo',
            'aris_url': 'https://aris.example.org/#default/repository/a.group.12',
            'aris_grupo_informes': '03 PROYECTOS',
            'modo_aris': 'ejecutar'
        }
        pagina_google, pagina_gpt, pagina_aris = (MagicMock(), MagicMock(), MagicMock())
        contexto = MagicMock()
        contexto.new_page.side_effect = [pagina_google, pagina_gpt, pagina_aris]
        with patch('modules.preflight.sync_playwright') as pw, \
             patch('modules.preflight.navegar_con_sesion') as navegar, \
             patch('modules.preflight.esperar_sesion') as sesion, \
             patch('modules.preflight.encontrar_frame_puente'), \
             patch('modules.preflight._esperar_editor'), \
             patch('modules.preflight._abrir_informes') as informes, \
             patch('modules.preflight._contexto_actual_informes',
                   return_value='03 PROYECTOS') as contexto_actual:
            pw.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = contexto
            sesion.side_effect = lambda pagina, nombre, url, configuracion, listo: listo()
            verificar_sesiones(cfg, MagicMock())

        self.assertEqual([call.args[1] for call in navegar.call_args_list],
                         ['sheets', 'chatgpt', 'aris'])
        self.assertEqual(sesion.call_count, 3)
        informes.assert_called_once_with(pagina_aris, cfg)
        contexto_actual.assert_called_once_with(pagina_aris, cfg)
        pagina_google.close.assert_called_once()
        pagina_gpt.close.assert_called_once()
        pagina_aris.close.assert_called_once()
        contexto.close.assert_called_once()

    def test_carpeta_aris_incorrecta_bloquea_pipeline(self):
        cfg = {
            'apps_script_url': 'https://script.google.com/macros/s/ejemplo/exec',
            'hero_gpt_url': 'https://chatgpt.com/g/ejemplo',
            'aris_url': 'https://aris.example.org/#default/repository/a.group.12',
            'aris_grupo_informes': '03 PROYECTOS',
        }
        contexto = MagicMock()
        with patch('modules.preflight.sync_playwright') as pw, \
             patch('modules.preflight.navegar_con_sesion'), \
             patch('modules.preflight.esperar_sesion',
                   side_effect=lambda pagina, nombre, url, c, listo: listo()), \
             patch('modules.preflight.encontrar_frame_puente'), \
             patch('modules.preflight._esperar_editor'), \
             patch('modules.preflight._abrir_informes'), \
             patch('modules.preflight._contexto_actual_informes',
                   return_value='CARPETA EQUIVOCADA'):
            pw.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value = contexto
            with self.assertRaisesRegex(RuntimeError, 'carpeta de informes incorrecta'):
                verificar_sesiones(cfg, MagicMock())
        contexto.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
