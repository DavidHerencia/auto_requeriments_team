"""Auditoría de cruce de XML: pruebas sin navegador ni modificación de ARIS."""
import json
import tempfile
import unittest
from pathlib import Path

from tools.auditar_cruces_xml import auditar, datos_xml, ejecutar


def xml(code, name, tareas):
    tasks = "".join(
        f'<userTask id="T{i}" name="{t}"/>'
        for i, t in enumerate(tareas, 1)
    )
    return (
        '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">'
        f'<process id="Proceso" name="{code} {name}">'
        f'<laneSet><lane id="LanePrincipal" name="{code} {name}"/></laneSet>'
        f'{tasks}</process></definitions>'
    )


class TestCruces(unittest.TestCase):
    def test_detecta_xml_del_otro_n5_aunque_titulo_final_sea_correcto(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            a_code, b_code = "4.4.1.11_P01", "4.4.1.12_P01"
            a_tareas = ("Consultar solicitud", "Clasificar consulta",
                        "Comunicar respuesta", "Registrar respuesta")
            b_tareas = ("Amortizar prestamo", "Validar saldo",
                        "Registrar amortizacion", "Confirmar amortizacion")
            orig_a = xml(a_code, "Consultas", a_tareas)
            orig_b = xml(b_code, "Amortización", b_tareas)
            # El XML incorrecto conserva actividades de B pero el proceso
            # y la lane principal fueron renombrados por el postprocesamiento.
            final_a = xml(a_code, "Consultas", b_tareas)
            archivo = folder / "final_a.xml"
            archivo.write_text(final_a, encoding="utf-8")
            rows = [
                {"id_solicitud": "S_A", "id_n5": a_code,
                 "nombre_proceso": "Consultas", "xml": orig_a,
                 "xml_corregido_ruta": str(archivo)},
                {"id_solicitud": "S_B", "id_n5": b_code,
                 "nombre_proceso": "Amortización", "xml": orig_b},
            ]
            results = auditar(rows)
            a = results[0]
            self.assertEqual(a["estado"], "SOSPECHA_ALTA")
            self.assertIn("CONTENIDO_SE_PARECE_MAS_A_OTRA_SOLICITUD",
                          a["senales"])
            self.assertEqual(a["mejor_otra_solicitud"], "S_B")
            self.assertEqual(a["coincidencia_con_su_original"], 0.0)

    def test_detecta_codigo_ajeno_en_xml_que_vino_del_sheet(self):
        code = "4.4.1.11_P01"
        original = xml("4.4.1.12_P01", "Amortización",
                       ("Cuota", "Saldo", "Registro"))
        rows = [{"id_solicitud": "S1", "id_n5": code,
                 "nombre_proceso": "Consultas", "xml": original}]
        audit = auditar(rows)[0]
        self.assertEqual(audit["estado"], "SOSPECHA_ALTA")
        self.assertIn("XML_ORIGINAL_PROCESS_CON_OTRO_N5", audit["senales"])
        self.assertIn("XML_ORIGINAL_LANE_CON_OTRO_N5", audit["senales"])

    def test_sin_indicios_no_afirma_validez_absoluta(self):
        with tempfile.TemporaryDirectory() as d:
            code = "4.3.2.20_P01"
            tasks = ("Recibir solicitud", "Revisar contrato",
                     "Validar cancelacion")
            same = xml(code, "Cancelación", tasks)
            p = Path(d) / "final.xml"
            p.write_text(same, encoding="utf-8")
            audit = auditar([{"id_solicitud": "S1", "id_n5": code,
                             "nombre_proceso": "Cancelación",
                             "xml": same,
                             "xml_corregido_ruta": str(p)}])[0]
            self.assertEqual(audit["estado"], "SIN_INDICIOS_EN_ESTA_AUDITORIA")

    def test_original_malformado_pero_actividades_recuperables(self):
        # El XML original puede no parsear: es precisamente por ello que
        # se envió a corregir a HERO. No debemos perder esa evidencia.
        code = "4.4.1.1_P02"
        origen = xml(code, "Consulta Cuentas Ahorro PJ", (
            "Consultar cuenta", "Validar titular",
            "Revisar saldos", "Mostrar resultado"
        )) + "<etiqueta-sin-cerrar"
        extraido = datos_xml(origen)
        self.assertEqual(extraido["metodo"], "texto_recuperado")
        self.assertEqual(extraido["actividades"], 4)
        self.assertEqual(extraido["proceso"], f"{code} Consulta Cuentas Ahorro PJ")
        self.assertIn("consultar cuenta", extraido["nombres"])
        self.assertEqual(len(extraido["ids"]), 4)

    def test_reparacion_de_xml_malformado_sin_cambio_semantico(self):
        with tempfile.TemporaryDirectory() as d:
            code = "4.4.1.1_P02"
            tareas = ("Consultar cuenta", "Validar titular",
                      "Revisar saldos", "Mostrar resultado")
            original = xml(code, "Consulta Cuentas Ahorro PJ", tareas)
            original += "<etiqueta-sin-cerrar"
            final = Path(d) / "capturado.xml"
            final.write_text(
                xml(code, "Consulta Cuentas Ahorro PJ", tareas),
                encoding="utf-8"
            )
            row = {
                "id_solicitud": "S1", "id_n5": code,
                "nombre_proceso": "Consulta Cuentas Ahorro PJ",
                "xml": original, "xml_capturado_ruta": str(final),
            }
            result = auditar([row])[0]
            self.assertEqual(result["coincidencia_con_su_original"], 1.0)
            self.assertEqual(result["coincidencia_ids_con_original"], 1.0)
            self.assertEqual(result["metodo_lectura_original"], "texto_recuperado")
            self.assertNotIn("ACTIVIDADES_CAMBIARON", result["senales"])

    def test_capturado_equivocado_pero_corregido_conserva_ese_contenido(self):
        with tempfile.TemporaryDirectory() as d:
            code = "4.4.1.1_P02"
            original_tasks = ("Consultar cuenta", "Validar titular",
                              "Revisar saldos", "Mostrar resultado")
            other_tasks = ("Liquidar préstamo", "Registrar amortización",
                           "Validar intereses", "Notificar pago")
            origen = xml(code, "Consulta Ahorro", original_tasks)
            capturado = Path(d) / "capturado.xml"
            corregido = Path(d) / "corregido.xml"
            capturado.write_text(
                xml("4.4.1.11_P01", "Amortización", other_tasks),
                encoding="utf-8"
            )
            corregido.write_text(
                xml(code, "Consulta Ahorro", other_tasks), encoding="utf-8"
            )
            row = {
                "id_solicitud": "S1", "id_n5": code,
                "nombre_proceso": "Consulta Ahorro", "xml": origen,
                "xml_capturado_ruta": str(capturado),
                "xml_corregido_ruta": str(corregido),
            }
            result = auditar([row])[0]
            self.assertEqual(result["coincidencia_con_su_original"], 0.0)
            self.assertEqual(result["coincidencia_capturado_final"], 1.0)
            self.assertEqual(result["metodo_lectura_capturado"], "xml")
            self.assertIn("NOMBRES_E_IDS_DE_TAREAS_DIFERENTES",
                          result["senales"])

    def test_escanea_varios_checkpoints_y_detecta_cruce_entre_lotes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a, b = "4.4.1.11_P01", "4.4.1.12_P01"
            tareas_a = ("Registrar consulta", "Clasificar petición",
                        "Responder cliente", "Cerrar consulta")
            tareas_b = ("Evaluar amortización", "Consultar saldo",
                        "Aplicar cuota", "Registrar pago")
            final_a = root / "final_a.xml"
            final_a.write_text(xml(a, "Consultas", tareas_b), encoding="utf-8")
            lote_a = root / "procesados_lote_A.json"
            lote_b = root / "procesados_lote_B.json"
            lote_a.write_text(json.dumps({"registros": [{
                "id_solicitud": "S_A", "id_n5": a, "nombre_proceso": "Consultas",
                "xml": xml(a, "Consultas", tareas_a),
                "xml_corregido_ruta": str(final_a),
            }]}), encoding="utf-8")
            lote_b.write_text(json.dumps({"registros": [{
                "id_solicitud": "S_B", "id_n5": b, "nombre_proceso": "Amortización",
                "xml": xml(b, "Amortización", tareas_b),
            }]}), encoding="utf-8")
            _, salida_json, resumen = ejecutar(directorio=root)
            self.assertEqual(sum(resumen.values()), 2)
            data = json.loads(salida_json.read_text(encoding="utf-8"))
            sospechoso = data["registros"][0]
            self.assertEqual(sospechoso["estado"], "SOSPECHA_ALTA")
            self.assertIn("procesados_lote_B.json::S_B",
                          sospechoso["mejor_otra_solicitud"])
            self.assertEqual(len(data["fuentes"]), 2)

    def test_reporte_guarda_resumen_sin_xml_crudo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            checkpoint = root / "procesados_lote.json"
            original = xml("4.4.1.11_P01", "Consultas",
                           ("Entrada", "Revisar", "Fin de tramite"))
            checkpoint.write_text(json.dumps({
                "registros": [{"id_solicitud": "S1",
                               "id_n5": "4.4.1.11_P01",
                               "nombre_proceso": "Consultas",
                               "xml": original}]
            }, ensure_ascii=False), encoding="utf-8")
            csv_path, json_path, resumen = ejecutar(checkpoint)
            self.assertTrue(csv_path.is_file())
            self.assertTrue(json_path.is_file())
            self.assertNotIn("<definitions", json_path.read_text("utf-8"))
            self.assertEqual(sum(resumen.values()), 1)


if __name__ == "__main__":
    unittest.main()
