"""Pruebas de las correcciones de Chrome y del diálogo GWT sin ARIS real."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

from aris import Aris, Item, NoConfirmado


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

    def test_diagrama_y_carpeta_usan_mismo_selector_de_nombre(self):
        """El icono de GWT indica el tipo, no la clase .titledesc."""
        for icon_label, expected_kind in [
            ("folder web", "grupo"), ("model", "modelo")
        ]:
            with self.subTest(icon_label=icon_label):
                row = MagicMock()
                title = MagicMock()
                title.count.return_value = 1
                title.get_attribute.return_value = "Nombre exacto"
                icon = MagicMock()
                icon.count.return_value = 1
                icon.get_attribute.return_value = icon_label
                row.locator.side_effect = lambda sel: (
                    title if sel == ".itemTitle .titledesc[title]" else icon
                )
                self.assertEqual(
                    Aris._registro_fila(row), Item("Nombre exacto", expected_kind)
                )

    def test_copia_se_pega_con_destino_explicito_y_no_el_origen(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.abrir = Mock()
        bot.seleccionar = Mock()
        bot._toolbar = Mock(return_value=MagicMock())
        bot._pegar_una_vez = Mock()
        bot._alerta_destino_invalido = Mock(return_value=False)
        bot._volver_a_destino_tras_copia = Mock()
        bot.listar = Mock(side_effect=[
            [Item("00 Ejemplo", "grupo")],
            [Item("00 Ejemplo", "grupo"),
             Item("Copia de 00 Ejemplo", "grupo")],
        ])
        copiada = bot.copiar_plantilla(
            "https://aris.example/lote", "Procesados", "00 Ejemplo",
            "https://aris.example/raiz", "Raíz"
        )
        self.assertEqual(copiada, "Copia de 00 Ejemplo")
        self.assertEqual(bot.abrir.call_args_list, [
            call("https://aris.example/lote", expected="Procesados"),
            call("https://aris.example/raiz", expected="Raíz"),
        ])
        bot._volver_a_destino_tras_copia.assert_called_once_with(
            destino_url="https://aris.example/lote",
            destino="Procesados", nivel_padre="Raíz"
        )
        self.assertEqual(bot.seleccionar.call_args_list, [
            call("00 Ejemplo", "grupo"), call("Procesados", "grupo")
        ])
        bot._toolbar.assert_called_once_with("COPY")
        bot._pegar_una_vez.assert_called_once()

    def test_volver_a_destino_usa_navegacion_ui_y_no_hash(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.page.url = "https://aris.example/raiz"
        bot._grupo_actual = Mock(return_value="Raíz")
        bot._row = Mock()
        bot._ready = Mock()
        bot.esperar_overlay_copia = Mock()
        link = MagicMock()
        link.count.return_value = 1

        def set_destino(**kwargs):
            self.assertEqual(kwargs["timeout"], 120000)
            bot.page.url = "https://aris.example/lote"
        link.dblclick.side_effect = set_destino
        bot._row.return_value.locator.return_value = link

        bot._volver_a_destino_tras_copia(
            "https://aris.example/lote", "Procesados", "Raíz"
        )
        bot._row.assert_called_once_with("Procesados", "grupo")
        bot._row.return_value.locator.assert_called_once_with(
            ".itemTitle .titledesc a.clickable"
        )
        bot.esperar_overlay_copia.assert_called_once_with(timeout_seconds=300)
        link.dblclick.assert_called_once_with(timeout=120000)
        bot._ready.assert_called_once_with("Procesados", timeout=60000)

    def test_no_entra_dos_veces_si_aris_ya_esta_en_destino(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.page.url = "https://aris.example/lote"
        bot._grupo_actual = Mock(return_value="Procesados")
        bot._row = Mock()
        bot._ready = Mock()
        bot._volver_a_destino_tras_copia(
            "https://aris.example/lote", "Procesados", "Raíz"
        )
        bot._row.assert_not_called()
        bot._ready.assert_called_once_with("Procesados", timeout=45000)

    def test_nivel_inesperado_detiene_copia_sin_navegar(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot._grupo_actual = Mock(return_value="Otra carpeta")
        bot._row = Mock()
        with self.assertRaisesRegex(NoConfirmado, "Otra carpeta"):
            bot._volver_a_destino_tras_copia(
                "https://aris.example/lote", "Procesados", "Raíz"
            )
        bot._row.assert_not_called()

    def test_url_regenerada_no_bloquea_si_el_contexto_es_correcto(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.page.url = "https://aris.example/ruta-regenerada"
        bot._grupo_actual = Mock(return_value="Procesados")
        bot._ready = Mock()
        bot._row = Mock()
        bot._volver_a_destino_tras_copia(
            "https://aris.example/lote", "Procesados", "Raíz"
        )
        bot._ready.assert_called_once_with("Procesados", timeout=45000)
        bot._row.assert_not_called()

    def test_espera_estructura_hasta_que_ambos_grupos_sean_visibles(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.entrar = Mock()
        # Forzar la rama alternativa: la copia ya está abierta.
        bot._grupo_actual = Mock(return_value="00 Ejemplo(1)")
        bot.subir_nivel = Mock()
        bot.listar = Mock(side_effect=[
            [Item("00 Procesos", "grupo")],
            [Item("00 Procesos", "grupo"),
             Item("01 Mapas y Catálogos", "grupo")],
        ])
        bot.validar_copia_completa(
            "https://aris.example/lote", "Procesados", "00 Ejemplo(1)"
        )
        self.assertEqual(bot.listar.call_count, 2)
        bot.entrar.assert_called_once_with(
            "https://aris.example/lote", "Procesados", "00 Ejemplo(1)"
        )
        bot.subir_nivel.assert_called_once_with("Procesados")

    def test_validar_copia_desde_lote_no_cambia_hash(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot._grupo_actual = Mock(side_effect=["Procesados", "00 Ejemplo(1)"])
        bot._row = Mock()
        bot._ready = Mock()
        bot.entrar = Mock()
        bot.subir_nivel = Mock()
        bot.esperar_overlay_copia = Mock()
        link = MagicMock()
        link.count.return_value = 1
        bot._row.return_value.locator.return_value = link
        bot.listar = Mock(return_value=[
            Item("00 Procesos", "grupo"),
            Item("01 Mapas y Catálogos", "grupo"),
        ])
        bot.validar_copia_completa(
            "https://aris.example/lote", "Procesados", "00 Ejemplo(1)"
        )
        bot.entrar.assert_not_called()
        bot._row.assert_called_once_with("00 Ejemplo(1)", "grupo")
        bot.esperar_overlay_copia.assert_called_once_with(timeout_seconds=300)
        link.dblclick.assert_called_once_with(timeout=120000)
        bot._ready.assert_called_once_with("00 Ejemplo(1)", timeout=60000)
        bot.subir_nivel.assert_called_once_with("Procesados")

    def test_overlay_impide_navegar_y_no_hace_segundo_pegado(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.page.locator.return_value.count.side_effect = [1, 1, 0]
        bot.esperar_overlay_copia(timeout_seconds=300)
        self.assertEqual(bot.page.wait_for_timeout.call_count, 2)
        bot.page.wait_for_timeout.assert_any_call(500)

    def test_balance_final_espera_refresco_antes_de_continuar(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot._grupo_actual = Mock(return_value="Procesados")
        bot.abrir = Mock()
        bot.listar = Mock(side_effect=[
            [Item("00 Ejemplo", "grupo"),
             Item("00 Ejemplo(1)", "grupo")],
            [Item("00 Ejemplo", "grupo"),
             Item("00 Ejemplo(1)", "grupo"),
             Item("00 Ejemplo(2)", "grupo")],
        ])
        bot.esperar_grupos(
            "https://aris.example/lote", "Procesados",
            {"00 Ejemplo", "00 Ejemplo(1)", "00 Ejemplo(2)"}
        )
        self.assertEqual(bot.listar.call_count, 2)
        bot.abrir.assert_not_called()
        bot.page.wait_for_timeout.assert_called_once_with(400)

    def test_copia_rechazada_no_reintenta_pegado(self):
        bot = Aris(Path("."), Mock())
        bot.page = MagicMock()
        bot.abrir = Mock()
        bot.seleccionar = Mock()
        bot._toolbar = Mock(return_value=MagicMock())
        bot._pegar_una_vez = Mock()
        bot.listar = Mock(return_value=[Item("00 Ejemplo", "grupo")])
        bot._alerta_destino_invalido = Mock(side_effect=[False, True])
        with self.assertRaisesRegex(NoConfirmado, "rechazó la copia"):
            bot.copiar_plantilla(
                "https://aris.example/lote", "Procesados", "00 Ejemplo",
                "https://aris.example/raiz", "Raíz"
            )
        bot._pegar_una_vez.assert_called_once()



if __name__ == "__main__":
    unittest.main()
