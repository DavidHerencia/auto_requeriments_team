"""Pruebas sin acceso a ARIS."""
import unittest
from unittest.mock import Mock

from aris import Item, nombre_proceso, validar_url
from workflow import _mapear


class TestLogic(unittest.TestCase):
    def test_sufijo_con_y_sin_espacio(self):
        self.assertEqual(nombre_proceso("11.5.1.1_P01 Plan -Detalle"), "11.5.1.1_P01 Plan")
        self.assertEqual(nombre_proceso("11.5.1.1_P01 Plan - Detalle"), "11.5.1.1_P01 Plan")
        self.assertEqual(nombre_proceso("A - Subproceso - Detalle"), "A - Subproceso")

    def test_sin_sufijo_error(self):
        with self.assertRaises(ValueError):
            nombre_proceso("Diagrama sin sufijo")

    def test_validar_host(self):
        url = ("https://aris.live-03.nextgen.igrupobbva/"
               "#default/repository/a.group.TEST")
        self.assertEqual(validar_url(url), url)
        with self.assertRaises(ValueError):
            validar_url("https://example.com/#default/repository/test")

    def test_mapear_no_incluye_plantilla_ni_otros(self):
        a = Mock()
        a.listar.return_value = [
            Item("00 Ejemplo", "grupo"), Item("A -Detalle", "grupo"),
            Item("B - Detalle", "grupo"), Item("Otra carpeta", "grupo"),
            Item("Un modelo", "modelo")]
        template, records = _mapear(a)
        self.assertEqual(template, "00 Ejemplo")
        self.assertEqual([r.base for r in records], ["A", "B"])

    def test_duplicados_despues_de_quitar_sufijo(self):
        a = Mock()
        a.listar.return_value = [
            Item("00 Ejemplo", "grupo"), Item("A -Detalle", "grupo"),
            Item("A - Detalle", "grupo")]
        with self.assertRaisesRegex(Exception, "repetidos"):
            _mapear(a)

    def test_requiere_plantilla(self):
        a = Mock()
        a.listar.return_value = [Item("A - Detalle", "grupo")]
        with self.assertRaisesRegex(Exception, "plantilla"):
            _mapear(a)


if __name__ == "__main__":
    unittest.main()
