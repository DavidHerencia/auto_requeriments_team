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


class TestArbolPublicacion(unittest.TestCase):
    def setUp(self):
        self.aris = MagicMock()
        self.log = Mock()
        self.bot = Publicador(self.aris, self.log)

    def test_escenario_despliega_00_procesos_antes_de_seleccionar(self):
        acciones = []
        self.bot._esperar_paso = Mock(
            side_effect=lambda *a: acciones.append(("paso", *a))
        )
        self.bot._expandir = Mock(
            side_effect=lambda *a: acciones.append(("triangulo", *a))
        )
        self.bot._seleccionar = Mock(
            side_effect=lambda *a: acciones.append(("nombre", *a))
        )
        self.bot._next = Mock(
            side_effect=lambda *a: acciones.append(("siguiente", *a))
        )
        detalle = "7.3.3.12_P01 Autenticación de clientes - Detalle"
        self.bot._escenario(detalle)
        self.assertEqual(acciones, [
            ("paso", "TREE_P0"),
            ("triangulo", "TREE_P0", "00 Procesos", 1),
            ("nombre", "TREE_P0", detalle, 2),
            ("siguiente", "Escenario / 00 Procesos"),
        ])

    def test_mapa_despliega_01_mapas_antes_de_seleccionar(self):
        acciones = []
        self.bot._esperar_paso = Mock(
            side_effect=lambda *a: acciones.append(("paso", *a))
        )
        self.bot._expandir = Mock(
            side_effect=lambda *a: acciones.append(("triangulo", *a))
        )
        self.bot._seleccionar = Mock(
            side_effect=lambda *a: acciones.append(("nombre", *a))
        )
        self.bot._next = Mock(
            side_effect=lambda *a: acciones.append(("siguiente", *a))
        )
        nombre = "7.3.3.12_P01 Autenticación de clientes"
        self.bot._mapa(nombre)
        self.assertEqual(acciones, [
            ("paso", "TREE_P1"),
            ("triangulo", "TREE_P1", "01 Mapas y Catálogos", 1),
            ("nombre", "TREE_P1", nombre, 2),
            ("siguiente", "Mapa / 01 Mapas y Catálogos"),
        ])

    def test_expandir_click_solo_en_imagen_triangulo(self):
        nodo = MagicMock()
        nodo.get_attribute.return_value = "false"
        triangulo = MagicMock()
        triangulo.count.return_value = 1
        nodo.locator.return_value.first = triangulo
        self.bot._esperar_nodo = Mock(return_value=nodo)
        self.bot._expandir("TREE_P0", "00 Procesos", 1)
        nodo.locator.assert_called_once_with(".cellTreeItemImage img")
        triangulo.click.assert_called_once_with()
        self.aris.page.wait_for_function.assert_called_once()

    def test_expandido_no_hace_segundo_click(self):
        nodo = MagicMock()
        nodo.get_attribute.return_value = "true"
        self.bot._esperar_nodo = Mock(return_value=nodo)
        self.bot._expandir("TREE_P0", "00 Procesos", 1)
        nodo.locator.assert_not_called()

    def test_seleccionar_hace_click_en_nombre_no_triangulo(self):
        nodo = MagicMock()
        etiqueta = MagicMock()
        etiqueta.count.return_value = 1
        etiqueta.get_attribute.return_value = "7.3.3.12_P01 - Detalle"
        nodo.locator.return_value.first = etiqueta
        self.bot._esperar_nodo = Mock(return_value=nodo)
        self.bot._seleccionar(
            "TREE_P0", "7.3.3.12_P01 - Detalle", 2
        )
        nodo.locator.assert_called_once_with(
            ".cellTreeItemValue > span[title]"
        )
        etiqueta.click.assert_called_once_with()
        self.aris.page.wait_for_function.assert_called_once()

    def test_siguiente_espera_activacion_una_sola_vez(self):
        boton = MagicMock()
        self.bot._boton = Mock(return_value=boton)
        self.bot._next("Escenario / 00 Procesos")
        self.aris.page.wait_for_function.assert_called_once()
        boton.click.assert_called_once_with()

    def test_siguiente_deshabilitado_no_se_pulsa(self):
        from playwright.sync_api import TimeoutError as PWTimeout
        self.bot._boton = Mock()
        self.aris.page.wait_for_function.side_effect = PWTimeout("Sin habilitar")
        with self.assertRaisesRegex(NoConfirmado, "no habilitó Siguiente"):
            self.bot._next("Escenario / 00 Procesos")
        self.bot._boton.assert_not_called()


class TestReanudacionDestinos(unittest.TestCase):
    def test_reutiliza_solo_n5_esperados_y_vacios(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        n5a = "7.3.3.12_P01"
        n5b = "7.3.3.13_P01"
        aris.page.url = "https://aris.example/#repositorio/04peru"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [Item(n5a, "grupo"), Item(n5b, "grupo")],
            [Item("90 OTROS MODELOS", "grupo")],
            [], [],
        ]
        disponibles = _verificar_destino(
            aris, "https://aris.example/#repositorio/atencion_r",
            "atencion_r", "https://aris.example/#repositorio/origen",
            codigos_esperados={n5a, n5b}, continuar_vacios=True,
        )
        self.assertEqual(disponibles, {n5a, n5b})
        self.assertEqual(aris.entrar.call_count, 2)
        aris.crear_grupo.assert_not_called()

    def test_reanudacion_sin_autorizacion_es_rechazada(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.listar.return_value = [Item("7.3.3.12_P01", "grupo")]
        with self.assertRaisesRegex(ArisError, "Ya existen carpetas N5 del lote"):
            _verificar_destino(
                aris, "https://aris.example/destino",
                "atencion_r", "https://aris.example/origen",
                codigos_esperados={"7.3.3.12_P01"},
                continuar_vacios=False,
            )
        aris.crear_grupo.assert_not_called()

    def test_reanudacion_rechaza_destino_no_vacio(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.page.url = "https://aris.example/#repositorio/04peru"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [Item("7.3.3.12_P01", "grupo")],
            [Item("90 OTROS MODELOS", "grupo")],
            [Item("Modelo existente", "modelo")],
        ]
        with self.assertRaisesRegex(ArisError, "ya contiene"):
            _verificar_destino(
                aris, "https://aris.example/destino",
                "atencion_r", "https://aris.example/origen",
                codigos_esperados={"7.3.3.12_P01"},
                continuar_vacios=True,
            )
        aris.crear_grupo.assert_not_called()

    def test_carpetas_de_otras_solicitudes_no_bloquean_lote(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.page.url = "https://aris.example/#repositorio/04peru"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [Item("1.1.1_P01", "grupo"), Item("otro modelo", "modelo")],
            [Item("90 OTROS MODELOS", "grupo")],
        ]
        result = _verificar_destino(
            aris, "https://aris.example/destino",
            "atencion_r", "https://aris.example/origen",
            codigos_esperados={"7.3.3.12_P01", "7.3.3.13_P01"},
            continuar_vacios=False,
        )
        self.assertEqual(result, set())
        aris.entrar.assert_not_called()
        aris.crear_grupo.assert_not_called()

    def test_mixto_grupo_ajeno_y_n5_vacio_reutiliza_solo_n5(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.page.url = "https://aris.example/#repositorio/04peru"
        aris._grupo_actual.return_value = "PERU_PRODUCCION"
        aris.listar.side_effect = [
            [Item("Otro N5", "grupo"),
             Item("7.3.3.12_P01", "grupo")],
            [Item("90 OTROS MODELOS", "grupo")],
            [],
        ]
        result = _verificar_destino(
            aris, "https://aris.example/destino",
            "atencion_r", "https://aris.example/origen",
            codigos_esperados={"7.3.3.12_P01", "7.3.3.13_P01"},
            continuar_vacios=True,
        )
        self.assertEqual(result, {"7.3.3.12_P01"})
        aris.entrar.assert_called_once_with(
            "https://aris.example/destino", "atencion_r", "7.3.3.12_P01"
        )

    def test_colision_con_un_modelo_del_mismo_codigo_detiene(self):
        from workflow_prod import _verificar_destino

        aris = MagicMock()
        aris.listar.return_value = [Item("7.3.3.12_P01", "modelo")]
        with self.assertRaisesRegex(ArisError, "no son grupos"):
            _verificar_destino(
                aris, "https://aris.example/destino",
                "atencion_r", "https://aris.example/origen",
                codigos_esperados={"7.3.3.12_P01"},
                continuar_vacios=True,
            )
        aris.crear_grupo.assert_not_called()


class TestResumenExportacion(unittest.TestCase):
    def setUp(self):
        self.aris = MagicMock()
        self.bot = Publicador(self.aris, Mock())
        self.dialog = MagicMock()
        self.dialog.inner_text.return_value = "3. Resumen de datos a exportar"
        self.resumen = MagicMock()
        self.resumen.count.return_value = 1
        self.dialog.locator.return_value = self.resumen
        self.bot._dialogo = Mock(return_value=self.dialog)
        self.finalizar = MagicMock()
        self.bot._boton = Mock(return_value=self.finalizar)
        self.bot._esperar_paso = Mock()
        self.detalle = "7.3.3.12_P01 Autenticación de clientes - Detalle"
        self.base = "7.3.3.12_P01 Autenticación de clientes"

    def test_ambos_grupos_en_un_solo_campo_y_segundo_vacio(self):
        self.resumen.input_value.return_value = (
            "Grupos de escenario:\n"
            "[G] 7.3.3.12_P01 Autenticación de clientes - Detalle\n\n"
            "Grupos y Modelos de Catálogo:\n"
            "[G] 7.3.3.12_P01 Autenticación de clientes\n"
        )
        self.bot._exportar(self.detalle, self.base)
        self.dialog.locator.assert_called_once_with(
            '[data-testid="TEXTO_RESUMEN_P2"]'
        )
        self.finalizar.click.assert_called_once_with()
        self.bot._esperar_paso.assert_called_once_with(
            "TREE_P2", timeout=360000
        )

    def test_resumen_equivocado_no_pulsa_finalizar(self):
        self.resumen.input_value.return_value = (
            "Grupos de escenario:\n"
            "[G] 7.3.3.12_P01 Autenticación de clientes - Detalle\n\n"
            "Grupos y Modelos de Catálogo:\n"
            "[G] 7.3.3.13_P01 Otro mapa\n"
        )
        with self.assertRaisesRegex(ArisError, "No se pulsó Finalizar"):
            self.bot._exportar(self.detalle, self.base)
        self.finalizar.click.assert_not_called()


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

    def test_importacion_intermedia_y_aviso_final_dos_aceptar_distintos(self):
        bot, _, _, registrar = self.crear_publicador("no importa")
        codigo = "7.3.3.12_P01"
        ruta = (
            "04 PERU" + chr(92) + "90 OTROS MODELOS" + chr(92) +
            "atencion_r" + chr(92) + codigo
        )
        intermedia = MagicMock()
        intermedia.inner_text.return_value = (
            "Importación de escenario\n"
            f"La información se ha incorporado en la carpeta '{ruta}' "
            "pulse aceptar"
        )
        aceptar_import = MagicMock()
        aceptar_import.count.return_value = 1
        aceptar_import.get_attribute.return_value = None
        intermedia.get_by_role.return_value = aceptar_import

        final = MagicMock()
        final.inner_text.return_value = (
            "BBVA - Publicación de modelado de proceso\n"
            "El informe está completo.\nAceptar"
        )
        aceptar_final = MagicMock()
        aceptar_final.count.return_value = 1
        aceptar_final.get_attribute.return_value = None
        final.get_by_role.return_value = aceptar_final

        class DosDialogos:
            def __init__(self):
                self.items = iter((intermedia, final))
            def count(self):
                return 1
            @property
            def first(self):
                return next(self.items)

        dialogos = DosDialogos()
        otros = MagicMock()
        otros.count.return_value = 0
        bot.page.locator.side_effect = lambda selector: (
            dialogos if selector == 'div[role="dialog"]:visible' else otros
        )
        self.assertTrue(bot._resultado(baseline=0, codigo=codigo))
        aceptar_import.click.assert_called_once_with()
        aceptar_final.click.assert_called_once_with()
        bot.page.wait_for_function.assert_called_once()
        final.wait_for.assert_called_once_with(
            state="hidden", timeout=30000
        )
        self.assertTrue(any(
            "[PROD][IMPORTACION OK]" in call.args[0]
            for call in registrar.call_args_list
        ))
        self.assertTrue(any(
            "[PROD][RESULTADO FINAL]" in call.args[0]
            for call in registrar.call_args_list
        ))

    def test_importacion_intermedia_ruta_ajena_no_acepta(self):
        bot, _, aceptar, _ = self.crear_publicador(
            "Importación de escenario\n"
            "La información se ha incorporado en la carpeta "
            "'04 PERU\\90 OTROS MODELOS\\atencion_r\\OTRO_N5' "
            "pulse aceptar"
        )
        with self.assertRaisesRegex(NoConfirmado, "no confirma la ruta N5"):
            bot._resultado(baseline=0, codigo="7.3.3.12_P01")
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
