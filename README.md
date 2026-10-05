# JW Library Extractor

Extrae la información de los archivos `.jwlibrary` a formatos legibles y abiertos, sin modificar la copia de seguridad. Está escrito en Python y la extracción principal no necesita instalar dependencias.

## Uso

Requiere **Python 3.9 o posterior**.

```bash
git clone https://github.com/Ismola/jwlibrary-extractor.git
cd jwlibrary-extractor
python3 extract_jwlibrary.py /ruta/a/backup.jwlibrary
```

El resultado se guarda junto a la copia, en una carpeta nueva llamada `backup_extraido`. Para elegir otra carpeta:

```bash
python3 extract_jwlibrary.py /ruta/a/backup.jwlibrary --salida /ruta/a/resultados
```

`--output` es un alias de `--salida`. Si la carpeta ya existe, el programa se detiene para evitar sobrescribir una extracción anterior.

## Información que se extrae

| Información | Resultado legible |
|---|---|
| Notas | Markdown y texto plano con título, contenido, fechas, referencia y etiquetas; además JSON y CSV |
| Etiquetas | Índice Markdown con enlaces a las notas individuales y asociaciones en JSON/CSV |
| Campos de texto y respuestas escritas | Markdown, JSON y CSV, incluidos los campos vacíos |
| Marcadores | Título, fragmento, posición y referencia en Markdown, JSON y CSV |
| Subrayados | Ubicación, rangos de bloques y tokens, índices de color y estilo y notas asociadas en JSON/CSV |
| Favoritos | Ubicaciones y asociaciones en JSON/CSV |
| Listas de reproducción | Listas, orden, elementos, medios, duraciones, ubicaciones y marcadores en JSON/CSV |
| Imágenes | Archivos originales y copias con nombres de origen cuando están disponibles; galería Markdown |
| Base de datos | Todas las tablas, incluso las vacías o desconocidas, en JSON/CSV; volcado SQL, esquema y base original |
| Metadatos del respaldo | Manifiesto, inventario de archivos, fechas, tamaños, sumas SHA-256 y comprobaciones de integridad |

Las referencias bíblicas se muestran con el nombre del libro y, cuando la nota contiene esa referencia, capítulo y versículo. Para otras publicaciones se muestran los títulos existentes en el respaldo y los identificadores originales. Los códigos que no se pueden interpretar se conservan.

## Resultado

```text
backup_extraido/
├── LEEME.md                  # Informe, enlaces, recuentos y límites de la extracción
├── resumen.json
├── inventario_archivo.json
├── datos_completos.json      # Manifiesto y todas las tablas
├── datos_completos.sql       # Volcado para reconstruir la base
├── esquema.json
├── esquema.sql
├── extraer_jwlibrary.py      # Copia del script utilizado
├── originales/              # Contenido exacto del archivo .jwlibrary
│   ├── manifest.json
│   ├── userData.db
│   └── ...
├── tablas/
│   ├── metadatos.json
│   ├── json/
│   └── csv/
├── legible/
│   ├── notas.md
│   ├── notas.txt
│   ├── notas.json
│   ├── notas.csv
│   ├── notas/               # Un Markdown por nota
│   ├── indice_etiquetas.md
│   ├── campos_escritos.md
│   ├── marcadores.md
│   ├── subrayados.json
│   ├── listas_reproduccion.json
│   └── ...
└── imagenes/
    ├── galeria.md
    ├── indice.json
    └── ...
```

Empieza por `LEEME.md`, `legible/notas.md` o `legible/indice_etiquetas.md`. Los CSV usan UTF-8 con BOM para facilitar la apertura en hojas de cálculo; las estructuras anidadas se representan como JSON dentro de las celdas. JSON y SQL conservan la distinción entre `NULL` y texto vacío; en CSV ambos aparecen como celdas vacías.

## OCR opcional de las imágenes

Instala [Tesseract](https://github.com/tesseract-ocr/tesseract) y ejecuta:

```bash
python3 extract_jwlibrary.py /ruta/a/backup.jwlibrary --ocr
```

El script descarga el modelo español del repositorio oficial [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) y guarda los textos en `ocr_imagenes/`. El reconocimiento se ejecuta en tu equipo; la copia y las imágenes no se envían a ningún servicio. Si la descarga falla, intenta utilizar el modelo inglés instalado. Sin Tesseract, la extracción principal sigue funcionando.

El texto del OCR puede tener errores u omisiones, especialmente en mapas o letras pequeñas. Conserva las imágenes originales para consultar el contenido exacto. `--ocr` es la única función que necesita conexión de red.

## Qué se puede recuperar

Las copias examinadas contienen un ZIP con `manifest.json`, una base SQLite y archivos multimedia. El programa lee la base en modo de solo lectura, comprueba el CRC del ZIP, la integridad de SQLite y las claves foráneas, y verifica que el respaldo original conserva su SHA-256.

La extracción se ha validado con una copia de **esquema 16**. Las tablas ausentes de funciones opcionales se toleran; otros cambios en nombres o columnas de futuras versiones pueden requerir adaptar las vistas legibles. La exportación de tablas descubre el esquema de la base y no limita las tablas a una lista fija.

Los subrayados suelen contener posiciones y referencias, **sin el texto completo de la Biblia o de la publicación**. Esta herramienta exporta esas posiciones y el texto que exista en notas, títulos y fragmentos; no puede reconstruir textos que no estén dentro del respaldo. Tampoco recupera registros borrados. La base y todos los archivos originales se conservan byte por byte para otros análisis.

## Pruebas

```bash
python3 -m unittest discover -s tests -v
```

Las pruebas usan exclusivamente copias sintéticas creadas durante la ejecución: comprueban contenido Unicode y multilínea, relaciones entre registros, `NULL`, campos vacíos, datos binarios, reconstrucción SQL, copias vacías y rechazo de rutas inseguras. El repositorio no incluye copias ni información de usuarios.

## Licencia

[MIT](LICENSE). Proyecto independiente, sin afiliación con los desarrolladores de JW Library.
