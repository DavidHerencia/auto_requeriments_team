"""Tests sin ARIS real para bloques, checkpoint y mapeo de una pasada."""
import unittest
from unittest.mock import MagicMock, Mock

from aris import ArisError, Item
from workflow import Resultado
from workflow_batch import (
    BATCH_SIZE, _estado_incierto, _grupos_libres,
    _preparar_registros, _reconstruir, _verificar_checkpoint_estructura,
    bloques,
)
from workflow_prod import RegistroProd
from workflow_prod_batch import _estado_bloqueado

ROOT = "https://aris.live-03.nextgen.igrupobbva/#default/repository/test"


class TestBloques(unittest.TestCase):
    def test_99_diagramas_forman_10_bloques_ultimo_9(self):
        serie = list(range(99))
        chunks = list(bloques(serie))
        self.assertEqual(len(chunks), 10)
        self.assertEqual([len(c) for _, c in chunks], [10] * 9 + [9])
        self.assertEqual(
            [n for _, chunk in chunks for n in chunk],
            serie,
        )

    def test_bloques_no_crean_partes_vacias(self):
        self.assertEqual(list(bloques([])), [])
        self.assertEqual(len(list(bloques(range(1)))), 1)
        self.assertEqual(BATCH_SIZE, 10)

    def test_copias_antiguas_se_ordenan_numericamente(self):
        existentes = {
            "00 Ejemplo", "00 Ejemplo(46)", "00 Ejemplo(3)",
            "00 Ejemplo(11)", "Otro",
        }
        self.assertEqual(
            _grupos_libres(existentes, "00 Ejemplo"),
            ["00 Ejemplo(3)", "00 Ejemplo(11)", "00 Ejemplo(46)"],
        )

    def test_una_operacion_en_curso_no_se_reemite(self):
        r = Resultado(base="Proceso", carpeta_proceso="Proceso - Detalle")
        r.informe = "en_curso"
        self.assertTrue(_estado_incierto(r))
        r.informe = "ok"
        r.movimiento = "en_curso"
        self.assertTrue(_estado_incierto(r))

    def test_checkpoint_existente_si_grupo_no_esta_rechaza(self):
        r = Resultado(base="Proceso", carpeta_proceso="Proceso - Detalle")
        r.plantilla = "ok"
        with self.assertRaisesRegex(ArisError, "no lo muestra"):
            _verificar_checkpoint_estructura({"00 Ejemplo"}, r)

    def test_carpeta_sin_checkpoint_no_se_toma_automatica(self):
        r = Resultado(base="Proceso", carpeta_proceso="Proceso - Detalle")
        with self.assertRaisesRegex(ArisError, "no confirma"):
            _verificar_checkpoint_estructura({"00 Ejemplo", "Proceso"}, r)

    def test_legacy_json_puede_reconstruirse(self):
        original = {
            "base": "Proceso", "carpeta_proceso": "Proceso - Detalle",
            "plantilla": "pendiente", "normalizacion": "ok",
            "modelo_origen": "Proceso", "otro_campo": 12,
        }
        resultado = _reconstruir(original)
        self.assertEqual(resultado.plantilla, "pendiente")
        self.assertEqual(resultado.modelo_origen, "Proceso")
        self.assertEqual(resultado.batch, 0)
        self.assertEqual(resultado.estructura, "")

    def test_pase_prod_no_repite_informe_confirmado_o_incierto(self):
        for status in ("ok", "fallo_aris", "no_confirmado", "en_curso"):
            r = RegistroProd(base="A", codigo="1.2_P01")
            r.publicacion = status
            self.assertTrue(_estado_bloqueado(r))
        r.publicacion = "pendiente"
        self.assertFalse(_estado_bloqueado(r))


class TestMapeoUnico(unittest.TestCase):
    def _bot(self, nombres, modelos):
        aris = MagicMock()
        aris.listar.side_effect = [
            [Item(x, "grupo") for x in nombres],
            [Item("00 Procesos", "grupo"),
             Item("01 Mapas y Catálogos", "grupo")],
            *[[Item(model, "modelo")] for model in modelos],
        ]
        return aris

    def test_mapear_y_leer_modelos_sin_segundo_recorrido(self):
        a = "4.1_P01 Archivo - Detalle"
        b = "4.2_P01 Archivo2 - Detalle"
        models = ["4.1_P01 Diagrama: A", "4.2_P01 Diagrama/B"]
        aris = self._bot(["00 Ejemplo", a, b], models)
        doc = {}
        template, records = _preparar_registros(
            aris, ROOT, "Subir", doc, Mock(), True, Mock()
        )
        self.assertEqual(template, "00 Ejemplo")
        self.assertEqual([r.base for r in records], models)
        self.assertEqual(aris.listar.call_count, 4)
        aris.renombrar.assert_not_called()
        self.assertEqual(records[0].normalizacion, "planificado")

    def test_los_nombres_duplicados_se_detectan_sin_escrituras(self):
        a = "4.1_P01 Archivo - Detalle"
        b = "4.2_P01 Archivo2 - Detalle"
        aris = self._bot(["00 Ejemplo", a, b], ["Modelo X", "Modelo X"])
        with self.assertRaisesRegex(ArisError, "mismo nombre"):
            _preparar_registros(
                aris, ROOT, "Subir", {}, Mock(), False, Mock()
            )
        aris.renombrar.assert_not_called()

    def test_falta_plantilla_nueva_ejecucion_exige_reanudar(self):
        aris = MagicMock()
        aris.listar.return_value = [Item("Proceso - Detalle", "grupo")]
        with self.assertRaisesRegex(ArisError, "reanuda el JSON"):
            _preparar_registros(
                aris, ROOT, "Subir", {}, Mock(), False, Mock()
            )


if __name__ == "__main__":
    unittest.main()
