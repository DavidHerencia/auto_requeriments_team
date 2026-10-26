const SHEET_NAME = 'EXPORT_RDA';
const CAMPOS = ['id_solicitud','id_n5','nombre_proceso','xml'];
function doGet() { return HtmlService.createHtmlOutputFromFile('Bridge').setTitle('RDA XML'); }
function listarRegistros() {
  const libro = SpreadsheetApp.getActiveSpreadsheet();
  const hoja = libro && libro.getSheetByName(SHEET_NAME);
  if (!hoja) throw new Error('Falta la pestaña EXPORT_RDA');
  const tabla = hoja.getDataRange().getValues();
  if (!tabla.length) return [];
  const columnas = tabla[0].map(v => String(v).trim());
  for (const c of CAMPOS) if (columnas.filter(x => x === c).length !== 1) throw new Error('Columna requerida: '+c);
  const ix = CAMPOS.map(c => columnas.indexOf(c));
  const ids = new Set();
  return tabla.slice(1).map((fila, i) => {
    const campos = ix.map(j => String(fila[j] ?? ''));
    if (campos.every(x => !x.trim())) return null;
    if (campos.some(x => !x.trim())) throw new Error('Campos vacíos en fila '+(i+2));
    const [id_solicitud,id_n5,nombre_proceso,xml] = campos;
    if (ids.has(id_solicitud)) throw new Error('ID duplicado: '+id_solicitud);
    ids.add(id_solicitud);
    return {id_solicitud,id_n5,nombre_proceso,xml};
  }).filter(Boolean);
}
