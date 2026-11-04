"""Flujo 1: el nombre real del MODELO prevalece sobre el del XML.

Pruebas offline: no lanzan Chrome, no modifican ARIS.
"""
import unittest
from unittest.mock import MagicMock, Mock, call

from aris import ArisError, Item, NoConfirmado
from workflow import Resultado, _normalizar_nombres_origen

RAIZ = "https://aris.live-03.nextgen.igrupobbva/#default/repository/raiz"
NOMBRE_RAIZ = "Importados"


def record(nombre):
    return Resultado(
        base=nombre.split(" - Detalle")[0],
        carpeta_proceso=nombre,
        carpeta_inicial=nombre,
    )


def bot_con_grupos(grupos_raiz, *modelos_por_carpeta):
    bot = MagicMock()
    bot.listar.side_effect = [
        [Item(g, "grupo") for g in grupos_raiz],
        *[
            [Item(m, "modelo") for m in modelos]
            for modelos in modelos_por_carpeta
        ],
    ]
    return bot


class TestNormalizacionFlujo1(unittest.TestCase):
    def test_corrige_nombre_archivo_al_nombre_real_del_modelo(self):
        original = (
            "7.3.3.1_P02 Protección de datos- Gestión de bloqueos DLP - Detalle"
        )
        modelo = (
            "7.3.3.1_P02 Protección de datos: Gestión de bloqueos DLP"
        )
        esperado = modelo + " - Detalle"
        aris = bot_con_grupos(
            ["00 Ejemplo", original], [modelo],
        )
        registro = record(original)
        guardar = Mock()

        _normalizar_nombres_origen(
            aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
            simular=False, actualizar=guardar,
        )

        aris.entrar.assert_called_once_with(RAIZ, NOMBRE_RAIZ, original)
        aris.renombrar.assert_called_once_with(
            RAIZ, NOMBRE_RAIZ, original, esperado
        )
        self.assertEqual(registro.base, modelo)
        self.assertEqual(registro.modelo_origen, modelo)
        self.assertEqual(registro.carpeta_inicial, original)
        self.assertEqual(registro.carpeta_proceso, esperado)
        self.assertEqual(registro.normalizacion, "ok")
        self.assertEqual(registro.errores, [])
        self.assertGreater(guardar.call_count, 0)

    def test_simulacion_revisa_y_planifica_sin_escritura(self):
        original = "7.3.3.1_P02 Protección de datos- Gestión - Detalle"
        modelo = "7.3.3.1_P02 Protección de datos: Gestión"
        aris = bot_con_grupos(
            ["00 Ejemplo", original], [modelo],
        )
        registro = record(original)

        _normalizar_nombres_origen(
            aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
            simular=True,
        )

        aris.renombrar.assert_not_called()
        self.assertEqual(registro.normalizacion, "planificado")
        self.assertEqual(registro.base, modelo)
        self.assertEqual(registro.carpeta_proceso, original)

    def test_ya_coincide_no_se_renombra(self):
        modelo = "7.3.2.2_P01 Data Discovery"
        original = modelo + " - Detalle"
        aris = bot_con_grupos(["00 Ejemplo", original], [modelo])
        registro = record(original)

        _normalizar_nombres_origen(
            aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
            simular=False,
        )

        aris.renombrar.assert_not_called()
        self.assertEqual(registro.normalizacion, "sin_cambios")
        self.assertEqual(registro.base, modelo)

    def test_preserva_puntuacion_incluido_slash_y_backslash(self):
        original = "4.4.1.1_P04 Consulta A_B_C - Detalle"
        modelo = r"4.4.1.1_P04 Consulta A/B\\C: clientes"
        aris = bot_con_grupos(["00 Ejemplo", original], [modelo])
        registro = record(original)

        _normalizar_nombres_origen(
            aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
            simular=False,
        )

        aris.renombrar.assert_called_once_with(
            RAIZ, NOMBRE_RAIZ, original, modelo + " - Detalle",
        )

    def test_dos_modelos_con_mismo_nombre_detiene_antes_de_escribir(self):
        a, b = "4.1_P01 Archivo A - Detalle", "4.1_P02 Archivo B - Detalle"
        modelo = "4.1_P01 Modelo compartido"
        aris = bot_con_grupos(
            ["00 Ejemplo", a, b], [modelo], [modelo],
        )
        registros = [record(a), record(b)]

        with self.assertRaisesRegex(ArisError, "mismo nombre"):
            _normalizar_nombres_origen(
                aris, RAIZ, NOMBRE_RAIZ, registros, Mock(), simular=False,
            )
        aris.renombrar.assert_not_called()

    def test_carpeta_destino_existente_detiene_antes_de_escribir(self):
        alias = "4.1_P01 Archivo_A - Detalle"
        modelo = "4.1_P01 Archivo:A"
        esperado = modelo + " - Detalle"
        aris = bot_con_grupos(
            ["00 Ejemplo", alias, esperado], [modelo],
        )
        registro = record(alias)

        with self.assertRaisesRegex(ArisError, "ya existe"):
            _normalizar_nombres_origen(
                aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(), simular=False,
            )
        aris.renombrar.assert_not_called()

    def test_varias_carpetas_se_inspeccionan_antes_del_primer_renombrado(self):
        a = "3.1_P01 A-B - Detalle"
        b = "3.2_P01 X_Y - Detalle"
        modelo_a = "3.1_P01 A:B"
        modelo_b = "3.2_P01 X/Y"
        aris = MagicMock()
        eventos = []
        aris.listar.side_effect = [
            [Item("00 Ejemplo", "grupo"), Item(a, "grupo"), Item(b, "grupo")],
            [Item(modelo_a, "modelo")],
            [Item(modelo_b, "modelo")],
        ]
        aris.entrar.side_effect = lambda *_: eventos.append("entrar")
        aris.renombrar.side_effect = lambda *_: eventos.append("renombrar")
        registros = [record(a), record(b)]

        _normalizar_nombres_origen(
            aris, RAIZ, NOMBRE_RAIZ, registros, Mock(), simular=False,
        )

        self.assertEqual(eventos, [
            "entrar", "entrar", "renombrar", "renombrar",
        ])
        self.assertEqual(registros[0].base, modelo_a)
        self.assertEqual(registros[1].base, modelo_b)
        self.assertEqual(aris.renombrar.call_count, 2)

    def test_sin_modelo_no_renombra_ni_continua(self):
        original = "4.1_P01 Sin modelo - Detalle"
        aris = bot_con_grupos(["00 Ejemplo", original], [])
        registro = record(original)

        with self.assertRaisesRegex(ArisError, "exactamente 1 modelo"):
            _normalizar_nombres_origen(
                aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
                simular=False,
            )
        aris.renombrar.assert_not_called()
        self.assertEqual(registro.normalizacion, "error_inspeccion")

    def test_varios_modelos_no_adivina(self):
        original = "4.1_P01 Dos modelos - Detalle"
        aris = bot_con_grupos(
            ["00 Ejemplo", original], ["Modelo uno", "Modelo dos"],
        )
        with self.assertRaisesRegex(ArisError, "exactamente 1 modelo"):
            _normalizar_nombres_origen(
                aris, RAIZ, NOMBRE_RAIZ, [record(original)], Mock(),
                simular=False,
            )
        aris.renombrar.assert_not_called()

    def test_renombrado_no_confirmado_no_reintenta(self):
        original = "4.1_P01 Texto_A - Detalle"
        modelo = "4.1_P01 Texto:A"
        aris = bot_con_grupos(
            ["00 Ejemplo", original], [modelo],
        )
        aris.renombrar.side_effect = NoConfirmado("ARIS no confirmó")
        registro = record(original)

        with self.assertRaises(NoConfirmado):
            _normalizar_nombres_origen(
                aris, RAIZ, NOMBRE_RAIZ, [registro], Mock(),
                simular=False,
            )
        aris.renombrar.assert_called_once()
        self.assertEqual(registro.normalizacion, "no_confirmado")
        self.assertEqual(registro.carpeta_proceso, original)
        self.assertEqual(len(registro.errores), 1)


if __name__ == "__main__":
    unittest.main()
