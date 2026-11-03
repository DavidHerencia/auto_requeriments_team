"""Pruebas de las correcciones de Chrome y del diálogo GWT sin ARIS real."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from aris import Aris, Item


class TestInteracciones(unittest.TestCase):
    def test_chrome_se_inicia_con_sandbox_activado(self):
        bot = Aris(Path(tempfile.gettempdir()) / "rda_fake_profile", Mock())
        fake_pw = MagicMock()
        fake = MagicMock()
        fake.chromium.launch_persistent_context.return_value = MagicMock(
            pages=[MagicMock()]
        )
        fake_pw.start.return_value = fake
        with patch("aris.sync_playwright", return_value=fake_pw):
            with bot:
                kwargs = fake.chromium.launch_persistent_context.call_args.kwargs
                self.assertTrue(kwargs["chromium_sandbox"])
                self.assertEqual(kwargs["channel"], "chrome")
                self.assertNotIn("--no-sandbox", kwargs.get("args", []))

    def test_dialogo_escribe_teclas_y_un_solo_aceptar(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        dialog = MagicMock()
        field = MagicMock()
        field.count.return_value = 1
        field.input_value.return_value = "Procesados 03-11 09-20"
        accept = MagicMock()
        accept.count.return_value = 1

        def lookup(selector):
            if selector.startswith("input[type="):
                return field
            if selector.startswith(".modal-footer"):
                return accept
            raise AssertionError(selector)

        dialog.locator.side_effect = lookup
        bot._dialog = Mock(return_value=dialog)
        bot._guardar_dialogo("Procesados 03-11 09-20")

        field.fill.assert_called_once_with("")
        field.press_sequentially.assert_called_once_with(
            "Procesados 03-11 09-20", delay=12
        )
        bot.page.wait_for_function.assert_called_once()
        accept.click.assert_called_once()

    def test_crear_grupo_sube_al_padre_y_no_entra_dos_veces(self):
        parent = "https://aris.example/#default/repository/padre"
        child = "https://aris.example/#default/repository/nuevo"
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.page.url = child
        bot.abrir = Mock()
        bot.listar = Mock(return_value=[])
        bot._toolbar = Mock(return_value=MagicMock())
        bot._guardar_dialogo = Mock()
        bot._ready = Mock()
        bot.subir_nivel = Mock()
        bot._esperar_nombre = Mock()

        self.assertEqual(bot.crear_grupo(parent, "Padre", "Nuevo"), child)
        bot._toolbar.assert_called_once_with("CREATE_GROUP")
        bot._guardar_dialogo.assert_called_once_with("Nuevo")
        bot._ready.assert_called_once_with("Nuevo", timeout=60000)
        bot.subir_nivel.assert_called_once_with("Padre")
        bot._esperar_nombre.assert_called_once_with("Nuevo")

    def test_subir_nivel_utiliza_la_flecha_aris(self):
        bot = Aris(Path("."), Mock())
        button = MagicMock()
        bot._toolbar = Mock(return_value=button)
        bot._ready = Mock()
        bot.subir_nivel("Raíz")
        bot._toolbar.assert_called_once_with("LEVEL_UP")
        button.click.assert_called_once()
        bot._ready.assert_called_once_with("Raíz", timeout=60000)


if __name__ == "__main__":
    unittest.main()
