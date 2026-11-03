"""Pruebas locales del flujo 2. No abren ARIS ni publican modelos."""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from aris import Aris, ArisError, NoConfirmado, Item
from publicacion import Publicador
from workflow_prod import extraer_codigo, _plan


class TestPlanProd(unittest.TestCase):
    def test_codigo_desde_nombre_largo(self):
        self.assertEqual(
            extraer_codigo("3.3.1.1_P03 Diseño y mantenimiento de segmentos"),
            "3.3.1.1_P03",
        )
        self.assertEqual(
            extraer_codigo("11.5.1.1_P01 Proceso"),
            "11.5.1.1_P01",
        )

    def test_nombre_sin_codigo_no_se_publica(self):
        with self.assertRaises(ArisError):
            extraer_codigo("Carpeta ejemplo")

    def test_codigos_duplicados_no_se_publican(self):
        aris = MagicMock()
        aris.listar.return_value = [
            Item("3.3.1.1_P03 A", "grupo"),
            Item("3.3.1.1_P03 B", "grupo"),
        ]
        with self.assertRaisesRegex(ArisError, "comparten código"):
            _plan(aris, "https://aris.example/origen", "Origen", Mock())


class TestDialogoFinal(unittest.TestCase):
    def crear_publicador(self, texto, accept_count=1):
        aris = MagicMock()
        registrar = Mock()
        bot = Publicador(aris, registrar)

        dialogs = MagicMock()
        dialogs.count.return_value = 1
        dialog = MagicMock()
        dialogs.first = dialog
        dialog.inner_text.return_value = texto
        aceptar = MagicMock()
        aceptar.count.return_value = accept_count
        aceptar.get_attribute.return_value = None
        dialog.get_by_role.return_value = aceptar

        aris.page.locator.side_effect = lambda selector: (
            dialogs if selector == 'div[role="dialog"]:visible'
            else MagicMock()
        )
        return bot, dialog, aceptar, registrar

    def test_exito_cierra_aceptar_una_vez(self):
        bot, dialog, aceptar, registrar = self.crear_publicador(
            "BBVA - Publicación de modelado de proceso\nEl informe está completo.\nAceptar"
        )
        self.assertTrue(bot._resultado(baseline=0))
        aceptar.click.assert_called_once_with()
        dialog.wait_for.assert_called_once_with(state="hidden", timeout=30000)
        self.assertTrue(
            any("[PROD][RESULTADO FINAL]" in c.args[0]
                for c in registrar.call_args_list)
        )

    def test_fallo_tambien_cierra_y_no_reintenta(self):
        bot, dialog, aceptar, _ = self.crear_publicador(
            "BBVA - Publicación de modelado de proceso\n"
            "La generación del informe ha sido cancelada con errores.\nAceptar"
        )
        self.assertFalse(bot._resultado(baseline=0))
        aceptar.click.assert_called_once_with()
        dialog.wait_for.assert_called_once_with(state="hidden", timeout=30000)

    def test_completado_con_errores_se_considera_fallo(self):
        bot, _, aceptar, _ = self.crear_publicador(
            "El informe está completo con errores.\nAceptar"
        )
        self.assertFalse(bot._resultado(baseline=0))
        aceptar.click.assert_called_once_with()

    def test_sin_boton_aceptar_no_finge_exito(self):
        bot, _, aceptar, _ = self.crear_publicador(
            "El informe está completo.", accept_count=0
        )
        with self.assertRaises(NoConfirmado):
            bot._resultado(baseline=0)
        aceptar.click.assert_not_called()

    def test_mensaje_final_desconocido_no_hace_click(self):
        bot, _, aceptar, _ = self.crear_publicador(
            "BBVA - Publicación de modelado de proceso\\n"
            "Resultado especial sin clasificar\\nAceptar"
        )
        with self.assertRaisesRegex(NoConfirmado, "no se reconoció"):
            bot._resultado(baseline=0)
        aceptar.click.assert_not_called()

    def test_historial_anterior_no_sustituye_el_aviso_final(self):
        aris = MagicMock()
        bot = Publicador(aris, Mock())
        dialogs = MagicMock()
        dialogs.count.return_value = 0
        rows = MagicMock()
        rows.count.return_value = 2
        rows.nth.return_value.inner_text.return_value = (
            "BBVA - Publicación de modelado de proceso(07) "
            "La generación del informe ha concluido."
        )
        aris.page.locator.side_effect = lambda selector: (
            dialogs if selector == 'div[role="dialog"]:visible' else rows
        )
        with patch("publicacion.time.monotonic", side_effect=[0, 0, 1, 601]):
            with self.assertRaises(NoConfirmado):
                bot._resultado(baseline=1)
        aris.page.wait_for_timeout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
