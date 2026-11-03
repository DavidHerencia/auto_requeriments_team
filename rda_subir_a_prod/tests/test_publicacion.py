"""Pruebas locales del flujo 2. No abren ARIS ni publican modelos."""
import unittest
from itertools import count
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


class TestDestinoProduccion(unittest.TestCase):
    def test_valida_jerarquia_aunque_cabecera_mueste_peru_produccion(self):
        # ARIS muestra "Buscar en: PERU_PRODUCCION" mientras la tabla
        # representa los hijos de 04 PERU (entre ellos 90 OTROS MODELOS).
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.page.url = "https://aris.example/#repositorio/04PERU"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [],  # atencion_r inicialmente vacío
            [Item("90 OTROS MODELOS", "grupo"),
             Item("01 VISTAS", "grupo")],
        ]
        _verificar_destino(
            aris, "https://aris.example/#repositorio/atencion_r",
            "atencion_r", "https://aris.example/#repositorio/origen",
        )
        aris.subir_nivel.assert_called_once_with("90 OTROS MODELOS")
        aris.esperar_grupos.assert_called_once_with(
            "https://aris.example/#repositorio/04PERU",
            "90 OTROS MODELOS", {"atencion_r"},
        )
        aris._toolbar.assert_called_once_with("LEVEL_UP")
        aris._toolbar.return_value.click.assert_called_once()
        aris.abrir.assert_any_call(
            "https://aris.example/#repositorio/atencion_r",
            expected="atencion_r", reload=True
        )
        aris.crear_grupo.assert_not_called()

    def test_padre_incorrecto_falla_sin_crear_nada(self):
        from itertools import count
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.page.url = "https://aris.example/#repositorio/otro"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [], [Item("OTRO GRUPO", "grupo")],
        ]
        with patch("workflow_prod.time.monotonic",
                   side_effect=lambda: next(countador)):
            countador = count(start=0, step=50)
            with self.assertRaisesRegex(ArisError, "No se pudo confirmar"):
                _verificar_destino(
                    aris, "https://aris.example/#repositorio/atencion_r",
                    "atencion_r", "https://aris.example/#repositorio/origen",
                )
        aris.crear_grupo.assert_not_called()
        aris.abrir.assert_any_call(
            "https://aris.example/#repositorio/atencion_r",
            expected="atencion_r", reload=True
        )


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
        # Reloj virtual sin lista finita: el número de consultas a
        # monotonic() puede cambiar sin romper la prueba con StopIteration.
        ticks = count(start=0, step=100)
        with patch("publicacion.time.monotonic", side_effect=lambda: next(ticks)):
            with self.assertRaisesRegex(NoConfirmado, "no mostró un resultado final"):
                bot._resultado(baseline=1)

        # El historial, incluso con texto de éxito, no confirma el proceso
        # actual ni autoriza pulsar Aceptar. Solo se observó y se agotó el plazo.
        self.assertGreater(aris.page.wait_for_timeout.call_count, 0)
        aris.page.wait_for_timeout.assert_any_call(500)


if __name__ == "__main__":
    unittest.main()
