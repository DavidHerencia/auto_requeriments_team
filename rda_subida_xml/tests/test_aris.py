"""Contratos ARIS sin navegador ni datos corporativos."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from modules.aris import clasificar_mensaje, validar_xml_aris, _arise_url, seleccionar_informe, _abrir_informes, _contexto_actual_informes, ArisNoConfirmado, adjuntar_xml
import workflow
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from modules.aris import _opcion_auto, _normalizar_tipo_importacion, _resultado, _cerrar_pestanas_importacion


class TestAris(unittest.TestCase):
    def test_resultado_final_completo_clic_y_cierre(self):
        pagina, dialogo, boton = MagicMock(), MagicMock(), MagicMock()
        pagina.locator.return_value.all.return_value = [dialogo]
        dialogo.is_visible.return_value = True
        dialogo.inner_text.return_value = 'BBVA - Importar BPMN El informe está completo. Aceptar'
        dialogo.locator.return_value = boton
        boton.count.return_value = 1
        boton.is_visible.return_value = True
        boton.is_enabled.return_value = True
        boton.get_attribute.return_value = 'false'

        result = _resultado(pagina, {'aris_timeout_paso_ms': 15000})
        self.assertEqual(result['estado'], 'informe_completo')
        self.assertIsNone(result['importado'])  # Informe completo != modelo verificado
        boton.click.assert_called_once_with()
        dialogo.wait_for.assert_called_once_with(state='hidden', timeout=15000)

    def test_resultado_final_xml_rechazado_tambien_acepta(self):
        pagina, dialogo, boton = MagicMock(), MagicMock(), MagicMock()
        pagina.locator.return_value.all.return_value = [dialogo]
        dialogo.is_visible.return_value = True
        dialogo.inner_text.return_value = (
            'BBVA - Importar BPMN El fichero seleccionado no es válido. Aceptar'
        )
        dialogo.locator.return_value = boton
        boton.count.return_value = 1
        boton.is_visible.return_value = True
        boton.is_enabled.return_value = True
        boton.get_attribute.return_value = 'false'

        result = _resultado(pagina, {})
        self.assertEqual(result['estado'], 'xml_rechazado')
        self.assertFalse(result['importado'])
        boton.click.assert_called_once_with()
        dialogo.wait_for.assert_called_once_with(state='hidden', timeout=45000)

    def test_resultado_final_no_acepta_otro_popup(self):
        pagina, dialogo, boton = MagicMock(), MagicMock(), MagicMock()
        pagina.locator.return_value.all.return_value = [dialogo]
        # La primera lectura es válida, pero cambia durante la confirmación.
        dialogo.is_visible.return_value = True
        dialogo.inner_text.side_effect = [
            'El informe está completo.', 'Opciones de importación'
        ]
        dialogo.locator.return_value = boton
        boton.count.return_value = 1

        with self.assertRaisesRegex(ArisNoConfirmado, 'cambió antes de aceptar'):
            _resultado(pagina, {})
        boton.click.assert_not_called()

    def test_cierra_solo_pestana_nueva_aris_no_otros_tabs(self):
        principal = MagicMock()
        previa = MagicMock()
        nueva_modelo = MagicMock()
        nueva_modelo.url = 'https://aris.example.org/#default/model/1234'
        nueva_modelo.is_closed.return_value = False
        nueva_fuera = MagicMock()
        nueva_fuera.url = 'https://example.com/model'
        nueva_fuera.is_closed.return_value = False
        nueva_repo = MagicMock()
        nueva_repo.url = 'https://aris.example.org/#default/repository/a.group.A'
        nueva_repo.is_closed.return_value = False
        contexto = MagicMock()
        contexto.pages = [principal, previa, nueva_modelo, nueva_fuera, nueva_repo]

        cerradas = _cerrar_pestanas_importacion(
            contexto, principal, (principal, previa),
            'https://aris.example.org/#default/repository/a.group.B'
        )
        self.assertEqual(cerradas, 1)
        nueva_modelo.close.assert_called_once_with()
        previa.close.assert_not_called()
        principal.close.assert_not_called()
        nueva_fuera.close.assert_not_called()
        nueva_repo.close.assert_not_called()

    def test_resultados_terminales(self):
        self.assertEqual(clasificar_mensaje('El informe está completo.'), 'informe_completo')
        self.assertEqual(clasificar_mensaje('El fichero seleccionado no es válido. Pruebe de nuevo con otro fichero.'), 'xml_rechazado')
        self.assertEqual(clasificar_mensaje('Generando informes...'), 'requiere_revision')

    def test_configuracion_url_carpeta(self):
        self.assertEqual(
            _arise_url({'aris_url':'https://aris.example.org/#default/repository/a.group.ABC'}),
            'https://aris.example.org/#default/repository/a.group.ABC'
        )
        with self.assertRaises(ValueError):
            _arise_url({'aris_url':'http://aris.example.org/'})

    def test_xml_local_no_txt(self):
        with tempfile.TemporaryDirectory() as carpeta:
            root = Path(carpeta)
            archivo = root/'sample.xml'
            archivo.write_text('<semantic:definitions xmlns:semantic="urn:bpmn"/>', encoding='utf-8')
            self.assertEqual(validar_xml_aris(archivo), archivo.resolve())
            with self.assertRaises(ValueError):
                validar_xml_aris(root/'sample.txt')

    def test_checkpoint_importado_se_omite_al_reanudar(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'data'
            corregidos = data / 'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos / 'ejemplo - Detalle.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            origen = data / 'exportaciones' / 'procesados_prueba.json'
            origen.parent.mkdir(parents=True)
            origen.write_text(json.dumps({
                'version': 1, 'registros': [{
                    'id_solicitud':'S1',
                    'id_archivo_n5':'N5 Ejemplo - Detalle',
                    'estado':'correccion_finalizada',
                    'xml_corregido_ruta':str(xml),
                }, {
                    'id_solicitud':'S2',
                    'estado':'incompleto'
                }]
            }, ensure_ascii=False), encoding='utf-8')

            def importar(ruta, cfg, perfil, al_iniciar):
                al_iniciar()
                return {'estado':'informe_completo', 'importado':None}

            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'CHROME_PROFILE', data/'chrome_profile_sheets'), \
                 patch.object(workflow, 'procesar_aris', side_effect=importar) as llamador:
                destino = workflow.ejecutar_lote_aris(origen, {'modo_aris':'ejecutar'})
                estado = json.loads(destino.read_text(encoding='utf-8'))['registros'][0]
                self.assertEqual(estado['estado_aris'], 'informe_completo')
                self.assertEqual(estado['intentos_importacion'], 1)
                self.assertEqual(llamador.call_count, 1)
                # El segundo llamado no debe reimportar el mismo registro.
                workflow.ejecutar_lote_aris(origen, {'modo_aris':'ejecutar'})
                self.assertEqual(llamador.call_count, 1)

    def test_rechazo_no_dispara_reenvio_automatico(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            corregidos = data/'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos/'prueba.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            paquete = data/'exportaciones'/'procesados_sample.json'
            paquete.parent.mkdir()
            paquete.write_text(json.dumps({'registros':[{
                'id_solicitud':'S9','estado':'correccion_finalizada',
                'xml_corregido_ruta':str(xml)
            }]}), encoding='utf-8')
            def importar(*args, **kwargs):
                kwargs['al_iniciar']()
                return {'estado':'xml_rechazado','importado':False}

            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'procesar_aris', side_effect=importar) as mock:
                ruta = workflow.ejecutar_lote_aris(paquete, {})
                estado = json.loads(ruta.read_text(encoding='utf-8'))['registros'][0]
                self.assertEqual(estado['estado_aris'], 'xml_rechazado')
                self.assertEqual(estado['intentos_importacion'], 1)
                workflow.ejecutar_lote_aris(paquete, {})
                self.assertEqual(mock.call_count, 1)

    def test_dropdown_excluye_historial_aunque_tenga_nombre_igual(self):
        pagina = MagicMock()
        combo = MagicMock()
        historia = MagicMock()
        opcion = MagicMock()
        historia.is_visible.return_value = True
        opcion.is_visible.return_value = True
        # El historial no puede ser elegido; la opción del menú sí.
        historia.evaluate.return_value = False
        opcion.evaluate.return_value = True
        pagina.get_by_text.return_value.all.return_value = [historia, opcion]
        flecha = MagicMock()
        flecha.count.return_value = 1
        flecha.is_visible.return_value = True
        pagina.locator.return_value = flecha
        with patch('modules.aris._abrir_informes', return_value=combo), \
             patch('modules.aris._contexto_actual_informes', return_value='03 PROYECTOS'):
            grupo = seleccionar_informe(pagina, {})
        self.assertEqual(grupo, '03 PROYECTOS')
        opcion.click.assert_called_once_with()
        historia.click.assert_not_called()
        self.assertEqual(pagina.wait_for_function.call_count, 2)

    def test_contexto_distinto_en_preparar_no_bloquea_seleccion(self):
        pagina = MagicMock()
        panel = MagicMock()
        pagina.locator.return_value = panel
        pagina.wait_for_function.return_value.json_value.return_value = 'P00 Importación XML'
        cfg = {'modo_aris': 'preparar', 'aris_grupo_informes': '03 PROYECTOS'}
        with self.assertLogs('modules.aris', level='WARNING') as logs:
            actual = _contexto_actual_informes(pagina, cfg)
        self.assertEqual(actual, 'P00 Importación XML')
        self.assertIn('03 PROYECTOS', ''.join(logs.output))

    def test_contexto_distinto_en_ejecutar_bloquea_importacion(self):
        pagina = MagicMock()
        panel = MagicMock()
        etiqueta = MagicMock()
        etiqueta.is_visible.return_value = True
        etiqueta.inner_text.return_value = 'P00 Importación XML'
        panel.locator.return_value.all.return_value = [etiqueta]
        pagina.locator.return_value = panel
        pagina.wait_for_function.side_effect = PlaywrightTimeout('expiro')
        cfg = {'modo_aris': 'ejecutar', 'aris_grupo_informes': '03 PROYECTOS'}
        with self.assertRaisesRegex(ArisNoConfirmado, 'Carpeta incorrecta'):
            _contexto_actual_informes(pagina, cfg)

    def test_contexto_espera_grupo_real_y_no_placeholder(self):
        pagina = MagicMock()
        panel = MagicMock()
        pagina.locator.return_value = panel
        pagina.wait_for_function.return_value.json_value.return_value = '03 PROYECTOS'
        actual = _contexto_actual_informes(pagina, {
            'modo_aris': 'ejecutar',
            'aris_grupo_informes': '03 PROYECTOS',
            'aris_timeout_carpeta_ms': 120000,
        })
        self.assertEqual(actual, '03 PROYECTOS')
        self.assertEqual(pagina.wait_for_function.call_args.kwargs['arg'],
                         '03 PROYECTOS')
        self.assertEqual(pagina.wait_for_function.call_args.kwargs['timeout'],
                         120000)

    @patch('modules.aris._una_visible')
    def test_popup_opciones_click_real_y_exacto(self, unico):
        pagina = MagicMock()
        modal_lista = MagicMock()
        unico.return_value = modal_lista
        combo = MagicMock()
        combo.count.return_value = 1
        combo.input_value.return_value = 'Importación automatica'
        aceptar = MagicMock()
        aceptar.count.return_value = 1
        aceptar.is_visible.return_value = True
        aceptar.is_enabled.return_value = True
        aceptar.get_attribute.return_value = 'false'
        modal_lista.locator.side_effect = lambda sel: (
            combo if 'DROP_OPCIONES' in sel else aceptar)
        _opcion_auto(pagina, {'aris_tipo_importacion': 'Importación automática'})
        self.assertEqual(pagina.wait_for_function.call_args.kwargs['arg'],
                         'Importación automática')
        aceptar.click.assert_called_once_with()

    def test_normalizacion_tipo_importacion_aris(self):
        self.assertEqual(
            _normalizar_tipo_importacion('Importación automática'),
            _normalizar_tipo_importacion('Importación automatica')
        )
        self.assertEqual(
            _normalizar_tipo_importacion(' IMPORTACION   AUTOMATICA '),
            'importacion automatica'
        )
        self.assertNotEqual(
            _normalizar_tipo_importacion('Importación manual'),
            _normalizar_tipo_importacion('Importación automática')
        )

    @patch('modules.aris._una_visible')
    def test_modal_opciones_rechaza_manual_sin_click(self, unico):
        pagina = MagicMock()
        modal = MagicMock()
        unico.return_value = modal
        combo = MagicMock()
        combo.count.return_value = 1
        combo.input_value.return_value = 'Importación manual'
        aceptar = MagicMock()
        modal.locator.side_effect = lambda sel: (
            combo if 'DROP_OPCIONES' in sel else aceptar
        )
        with self.assertRaisesRegex(ArisNoConfirmado, 'cambió antes del clic'):
            _opcion_auto(pagina, {'aris_tipo_importacion': 'Importación automática'})
        aceptar.click.assert_not_called()

    @patch('modules.aris._esperar_transicion_archivo', return_value='opciones')
    @patch('modules.aris._aceptar')
    @patch('modules.aris._dialogo_con')
    def test_xml_seleccionado_avanza_a_opciones_sin_aceptar_dos_veces(
        self, get_dialogo, aceptar, transicion
    ):
        page = MagicMock()
        xml = Path('ejemplo.xml')
        archivo = MagicMock()
        archivo.count.return_value = 1
        archivo.evaluate.return_value = [xml.name]
        page.locator.return_value = archivo
        get_dialogo.return_value.count.return_value = 1
        get_dialogo.return_value.is_visible.return_value = True

        resultado = adjuntar_xml(page, xml, {})
        self.assertEqual(resultado, 'opciones')
        archivo.set_input_files.assert_called_once_with(str(xml))
        aceptar.assert_not_called()
        transicion.assert_called_once()

    @patch('modules.aris._esperar_transicion_archivo', return_value='rechazo')
    @patch('modules.aris._aceptar')
    @patch('modules.aris._dialogo_con')
    def test_xml_rechazado_no_confirma_dialogo(self, get_dialogo, aceptar, _trans):
        page = MagicMock()
        xml = Path('ejemplo.xml')
        archivo = MagicMock()
        archivo.count.return_value = 1
        archivo.evaluate.return_value = [xml.name]
        page.locator.return_value = archivo
        get_dialogo.return_value.count.return_value = 1
        get_dialogo.return_value.is_visible.return_value = True
        self.assertEqual(adjuntar_xml(page, xml, {}), 'rechazo')
        aceptar.assert_not_called()

    @patch('modules.aris._esperar_transicion_final_archivo', return_value='opciones')
    @patch('modules.aris._modal_activo_con_texto', return_value=False)
    @patch('modules.aris._esperar_transicion_archivo', return_value='aceptar_archivo')
    @patch('modules.aris._aceptar')
    @patch('modules.aris._dialogo_con')
    def test_aceptar_solo_una_vez_si_aun_sigue_modal_archivo(
        self, get_dialogo, aceptar, _trans, _modal, siguiente
    ):
        page = MagicMock()
        xml = Path('ejemplo.xml')
        archivo = MagicMock()
        archivo.count.return_value = 1
        archivo.evaluate.return_value = [xml.name]
        page.locator.return_value = archivo
        get_dialogo.return_value.count.return_value = 1
        get_dialogo.return_value.is_visible.return_value = True
        self.assertEqual(adjuntar_xml(page, xml, {}), 'opciones')
        aceptar.assert_called_once()
        siguiente.assert_called_once()

    @patch('modules.aris._esperar_transicion_archivo', return_value='aceptar_archivo')
    @patch('modules.aris._modal_activo_con_texto')
    @patch('modules.aris._aceptar')
    @patch('modules.aris._dialogo_con')
    def test_cambio_de_modal_antes_del_click_no_lo_repite(
        self, get_dialogo, aceptar, modal, _trans
    ):
        page = MagicMock()
        xml = Path('ejemplo.xml')
        archivo = MagicMock()
        archivo.count.return_value = 1
        archivo.evaluate.return_value = [xml.name]
        page.locator.return_value = archivo
        get_dialogo.return_value.count.return_value = 1
        get_dialogo.return_value.is_visible.return_value = True
        modal.side_effect = [True]
        self.assertEqual(adjuntar_xml(page, xml, {}), 'opciones')
        aceptar.assert_not_called()

    def test_pestana_activa_no_se_clickea_otra_vez(self):
        pagina = MagicMock()
        tab = MagicMock()
        panel = MagicMock()
        combo = MagicMock()
        tab.count.return_value = 1
        combo.count.return_value = 1
        panel.is_visible.return_value = True
        combo.is_visible.return_value = True
        pagina.locator.side_effect = lambda sel: (
            tab if sel == 'li[data-testid="REPORT_REPOSITORY_BUTTON"]'
            else panel if sel == '.sidebar.reportSidebar'
            else combo
        )
        self.assertIs(_abrir_informes(pagina, {}), combo)
        tab.locator.return_value.click.assert_not_called()

    def test_preparacion_fallida_reintenta_seleccion_sin_lanzar(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            corregidos = data/'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos/'proceso.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            origen = data/'exportaciones'/'procesados_test.json'
            origen.parent.mkdir()
            origen.write_text(json.dumps({'registros':[{
                'id_solicitud':'S20','estado':'correccion_finalizada',
                'xml_corregido_ruta':str(xml)
            }]}), encoding='utf-8')
            def mock_aris(*args, **kwargs):
                # No llama al callback al_iniciar: nunca lanzó el informe.
                return {'estado':'preparado_para_lanzar', 'importado':False}
            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'procesar_aris', side_effect=mock_aris) as mock:
                destino = workflow.ejecutar_lote_aris(origen, {'modo_aris':'preparar'})
                paquete = json.loads(destino.read_text(encoding='utf-8'))
                paquete['registros'][0]['estado_aris'] = 'preparacion_fallida'
                destino.write_text(json.dumps(paquete), encoding='utf-8')
                workflow.ejecutar_lote_aris(origen, {'modo_aris':'preparar'})
                estado = json.loads(destino.read_text(encoding='utf-8'))['registros'][0]
                self.assertEqual(estado['estado_aris'], 'pendiente')
                self.assertEqual(estado['intentos_importacion'], 0)
                self.assertEqual(mock.call_count, 2)

    def test_arise_iniciado_no_reintenta_importacion(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            corregidos = data/'corregidos'
            corregidos.mkdir(parents=True)
            xml = corregidos/'proceso.xml'
            xml.write_text('<definitions/>', encoding='utf-8')
            origen = data/'exportaciones'/'procesados_test.json'
            origen.parent.mkdir()
            origen.write_text(json.dumps({'registros':[{
                'id_solicitud':'S21','estado':'correccion_finalizada',
                'xml_corregido_ruta':str(xml)
            }]}), encoding='utf-8')
            with patch.object(workflow, 'DATA', data), \
                 patch.object(workflow, 'procesar_aris') as mock:
                destino = workflow.ejecutar_lote_aris(origen, {'modo_aris':'preparar'}, limite=1)
                paquete = json.loads(destino.read_text(encoding='utf-8'))
                paquete['registros'][0]['estado_aris'] = 'aris_iniciado'
                destino.write_text(json.dumps(paquete), encoding='utf-8')
                mock.reset_mock()
                workflow.ejecutar_lote_aris(origen, {'modo_aris':'ejecutar'})
                mock.assert_not_called()

    def test_no_importa_xml_fuera_de_carpeta(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)/'data'
            data.mkdir()
            externo = Path(tmp)/'privado.xml'
            externo.write_text('<definitions/>', encoding='utf-8')
            with patch.object(workflow, 'DATA', data):
                with self.assertRaises(ValueError):
                    workflow._xml_del_registro({'xml_corregido_ruta':str(externo)})


if __name__ == '__main__':
    unittest.main()
