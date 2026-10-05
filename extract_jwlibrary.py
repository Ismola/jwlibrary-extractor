#!/usr/bin/env python3
"""Extrae una copia JW Library sin modificarla; solo requiere Python estándar."""
import argparse
import base64
import csv
import hashlib
import json
import math
import re
import shutil
import sqlite3
import subprocess
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_value(value):
    if isinstance(value, bytes):
        return {"__type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    raise TypeError(type(value).__name__)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value) + "\n", encoding="utf-8")


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False, default=json_value)
                             if isinstance(row.get(key), (list, dict, bytes)) else row.get(key)
                             for key in columns})


def slug(text):
    return re.sub(r"[^\w .-]+", "_", text, flags=re.UNICODE).strip(" .")[:90] or "sin_nombre"


def sql_value(value):
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    if isinstance(value, str):
        # quote() de SQLite trunca en NUL; la lectura de texto suele convertir CR a LF.
        if "\x00" in value or "\r" in value:
            return "CAST(X'" + value.encode("utf-8").hex() + "' AS TEXT)"
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, float) and math.isinf(value):
        return "-9e999" if value < 0 else "9e999"
    return repr(value)


def write_sql(path, connection, tables):
    prefixes = {'INSERT INTO "' + name.replace('"', '""') + '" VALUES(': name for name in tables}
    written = set()
    with path.open("w", encoding="utf-8", newline="") as stream:
        # Conserva el orden de creación de tablas, índices y triggers de SQLite.
        for line in connection.iterdump():
            name = next((name for prefix, name in prefixes.items() if line.startswith(prefix)), None)
            if name is None:
                stream.write(line + "\n")
                continue
            if name in written:
                continue
            written.add(name)
            quoted = '"' + name.replace('"', '""') + '"'
            for row in tables[name]:
                values = ",".join(sql_value(value) for value in row.values())
                stream.write(f"INSERT INTO {quoted} VALUES({values});\n")


BOOKS = ["Génesis", "Éxodo", "Levítico", "Números", "Deuteronomio", "Josué", "Jueces", "Rut",
         "1 Samuel", "2 Samuel", "1 Reyes", "2 Reyes", "1 Crónicas", "2 Crónicas", "Esdras", "Nehemías",
         "Ester", "Job", "Salmos", "Proverbios", "Eclesiastés", "Cantar de los Cantares", "Isaías",
         "Jeremías", "Lamentaciones", "Ezequiel", "Daniel", "Oseas", "Joel", "Amós", "Abdías", "Jonás",
         "Miqueas", "Nahúm", "Habacuc", "Sofonías", "Ageo", "Zacarías", "Malaquías", "Mateo", "Marcos",
         "Lucas", "Juan", "Hechos", "Romanos", "1 Corintios", "2 Corintios", "Gálatas", "Efesios",
         "Filipenses", "Colosenses", "1 Tesalonicenses", "2 Tesalonicenses", "1 Timoteo", "2 Timoteo",
         "Tito", "Filemón", "Hebreos", "Santiago", "1 Pedro", "2 Pedro", "1 Juan", "2 Juan", "3 Juan",
         "Judas", "Apocalipsis"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archivo", type=Path)
    parser.add_argument("--salida", "--output", type=Path, help="Carpeta nueva donde guardar los resultados")
    parser.add_argument("--ocr", action="store_true", help="OCR local; descarga el modelo español oficial si falta")
    args = parser.parse_args()
    source = args.archivo.resolve()
    output = (args.salida or source.with_name(source.stem + "_extraido")).resolve()
    if output.exists():
        raise SystemExit(f"La carpeta de salida ya existe: {output}")
    output.mkdir(parents=True)
    raw = output / "originales"
    raw.mkdir()
    original_hash = sha256(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        entries = archive.infolist()
        # Valida todas las rutas antes de escribir ninguna entrada.
        for entry in entries:
            destination = (raw / entry.filename).resolve()
            if not destination.is_relative_to(raw.resolve()) or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError(f"Ruta no permitida: {entry.filename}")
        if archive.testzip() is not None:
            raise ValueError("La comprobación CRC del archivo ZIP ha fallado")
        inventory = []
        for entry in entries:
            destination = raw / entry.filename
            if entry.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            data = archive.read(entry)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            inventory.append({"archivo": entry.filename, "bytes": len(data), "bytes_comprimidos": entry.compress_size,
                              "sha256": sha256(data), "crc32": f"{entry.CRC:08x}", "fecha_zip": list(entry.date_time),
                              "metodo_compresion": entry.compress_type, "atributos_externos": entry.external_attr,
                              "comentario_base64": base64.b64encode(entry.comment).decode(),
                              "extra_base64": base64.b64encode(entry.extra).decode()})
        write_json(output / "inventario_archivo.json", {"entradas": inventory,
                   "comentario_zip_base64": base64.b64encode(archive.comment).decode()})
    manifest = json.loads((raw / "manifest.json").read_text(encoding="utf-8"))
    database = (raw / manifest["userDataBackup"]["databaseName"]).resolve()
    if not database.is_relative_to(raw.resolve()):
        raise ValueError("Ruta de base de datos no permitida")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    schema = [dict(row) for row in connection.execute("SELECT type,name,tbl_name,rootpage,sql FROM sqlite_master ORDER BY type,name")]
    write_json(output / "esquema.json", schema)
    (output / "esquema.sql").write_text("\n\n".join(row["sql"] + ";" for row in schema if row["sql"]) + "\n", encoding="utf-8")
    tables = {}
    table_info = {}
    for entry in schema:
        if entry["type"] != "table":
            continue
        name = entry["name"]
        quoted = '"' + name.replace('"', '""') + '"'
        columns = [dict(row) for row in connection.execute(f"PRAGMA table_info({quoted})")]
        keys = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
        order = " ORDER BY " + ",".join('"' + key.replace('"', '""') + '"' for key in keys) if keys else ""
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {quoted}{order}")]
        tables[name] = rows
        table_info[name] = {"filas": len(rows), "columnas": columns,
                            "claves_foraneas": [dict(row) for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")]}
        write_json(output / "tablas" / "json" / f"{name}.json", rows)
        write_csv(output / "tablas" / "csv" / f"{name}.csv", rows, [row["name"] for row in columns])
    write_json(output / "tablas" / "metadatos.json", table_info)
    write_json(output / "datos_completos.json", {"manifest": manifest, "tablas": tables})
    write_sql(output / "datos_completos.sql", connection, tables)
    # Las versiones del esquema pueden omitir tablas de funciones más recientes.
    def table(name):
        return tables.get(name, [])

    locations = {row["LocationId"]: row for row in table("Location")}
    marks = {row["UserMarkId"]: row for row in table("UserMark")}
    tags = {row["TagId"]: row for row in table("Tag")}
    ranges = defaultdict(list)
    tag_maps = defaultdict(list)
    note_ids_by_mark = defaultdict(list)
    for row in table("BlockRange"):
        ranges[row["UserMarkId"]].append(row)
    for row in table("TagMap"):
        for key in ("NoteId", "LocationId", "PlaylistItemId"):
            if row[key] is not None:
                tag_maps[(key, row[key])].append({**row, "Tag": tags[row["TagId"]]})
    for row in table("Note"):
        if row["UserMarkId"] is not None:
            note_ids_by_mark[row["UserMarkId"]].append(row["NoteId"])

    def reference(location, block_type=0, block_id=None):
        if not location:
            return "Sin ubicación asociada"
        book = location["BookNumber"]
        if book and 1 <= book <= len(BOOKS):
            text = BOOKS[book - 1]
            if location["ChapterNumber"]:
                text += f" {location['ChapterNumber']}"
                if block_type == 2 and block_id is not None:
                    text += f":{block_id}"
            return text
        parts = [location["Title"]] if location["Title"] else []
        for key in ("KeySymbol", "IssueTagNumber", "DocumentId", "Track"):
            if location[key] is not None and location[key] != 0:
                parts.append(f"{key}={location[key]}")
        if block_id is not None:
            parts.append(f"bloque {block_id} (tipo {block_type})")
        return " | ".join(parts) or f"Ubicación {location['LocationId']}"

    readable = output / "legible"
    individual = readable / "notas"
    individual.mkdir(parents=True)
    notes = []
    combined_md = []
    combined_txt = []
    for row in table("Note"):
        mark = marks.get(row["UserMarkId"])
        location = locations.get(row["LocationId"] if row["LocationId"] is not None else mark["LocationId"] if mark else None)
        tag_links = tag_maps[("NoteId", row["NoteId"])]
        ref = reference(location, row["BlockType"], row["BlockIdentifier"])
        filename = f"{row['NoteId']:06d}_{slug(row['Title'] or 'Sin título')}.md"
        notes.append({**row, "Referencia": ref, "Ubicacion": location, "Marca": mark,
                      "Rangos": ranges[row["UserMarkId"]], "Etiquetas": tag_links, "ArchivoMarkdown": f"notas/{filename}"})
        title = (row["Title"] or "Sin título").replace("\n", " ").replace("\r", " ")
        tag_names = ", ".join(item["Tag"]["Name"] for item in tag_links) or "Sin etiquetas"
        text = (f"# {title}\n\nID: {row['NoteId']} · GUID: {row['Guid']}\n\n"
                f"Referencia: {ref}\n\nEtiquetas: {tag_names}\n\n"
                f"Creada: {row['Created']}\n\nModificada: {row['LastModified']}\n\n"
                f"Tipo de bloque: {row['BlockType']} · Identificador: {row['BlockIdentifier']}\n\n")
        if mark:
            text += f"Marca: {mark['UserMarkId']} · ColorIndex: {mark['ColorIndex']} · StyleIndex: {mark['StyleIndex']}\n\n"
        text += (row["Content"] or "") + "\n"
        (individual / filename).write_text(text, encoding="utf-8")
        combined_md.append(text)
        combined_txt.append(f"NOTA {row['NoteId']}\nTÍTULO: {row['Title'] or ''}\nREFERENCIA: {ref}\n"
                            f"ETIQUETAS: {tag_names}\nCREADA: {row['Created']}\nMODIFICADA: {row['LastModified']}\n\n{row['Content'] or ''}\n")
    (readable / "notas.md").write_text("\n\n---\n\n".join(combined_md), encoding="utf-8")
    (readable / "notas.txt").write_text("\n" + ("\n" + "=" * 80 + "\n\n").join(combined_txt), encoding="utf-8")

    datasets = {
        "notas": notes,
        "subrayados": [{**row, "Referencia": reference(locations.get(row["LocationId"])),
                         "Ubicacion": locations.get(row["LocationId"]), "Rangos": ranges[row["UserMarkId"]],
                         "NotasId": note_ids_by_mark[row["UserMarkId"]]} for row in table("UserMark")],
        "marcadores": [{**row, "Referencia": reference(locations.get(row["LocationId"]), row["BlockType"], row["BlockIdentifier"]),
                        "Ubicacion": locations.get(row["LocationId"]),
                        "Publicacion": locations.get(row["PublicationLocationId"])} for row in table("Bookmark")],
        "campos_escritos": [{**row, "Referencia": reference(locations.get(row["LocationId"])),
                             "Ubicacion": locations.get(row["LocationId"])} for row in table("InputField")],
        "etiquetas": [{**row, "Asociaciones": [item for item in table("TagMap") if item["TagId"] == row["TagId"]]}
                      for row in table("Tag")],
        "favoritos": [{**row, "Tag": tags[row["TagId"]], "Ubicacion": locations.get(row["LocationId"])}
                      for row in table("TagMap") if tags[row["TagId"]]["Type"] == 0],
    }
    media = {row["IndependentMediaId"]: row for row in table("IndependentMedia")}
    playlist_items = {}
    for row in table("PlaylistItem"):
        playlist_items[row["PlaylistItemId"]] = {
            **row, "Etiquetas": tag_maps[("PlaylistItemId", row["PlaylistItemId"])],
            "Medios": [{**item, "Medio": media.get(item["IndependentMediaId"])}
                       for item in table("PlaylistItemIndependentMediaMap") if item["PlaylistItemId"] == row["PlaylistItemId"]],
            "Ubicaciones": [{**item, "Ubicacion": locations.get(item["LocationId"])}
                            for item in table("PlaylistItemLocationMap") if item["PlaylistItemId"] == row["PlaylistItemId"]],
            "Marcadores": [item for item in table("PlaylistItemMarker") if item["PlaylistItemId"] == row["PlaylistItemId"]]}
    datasets["listas_reproduccion"] = [{**tag, "Elementos": [
        {"Posicion": item["Position"], "Elemento": playlist_items.get(item["PlaylistItemId"])}
        for item in sorted(table("TagMap"), key=lambda item: item["Position"])
        if item["TagId"] == tag["TagId"]]} for tag in table("Tag") if tag["Type"] == 2]
    datasets["elementos_reproduccion"] = list(playlist_items.values())
    for name, rows in datasets.items():
        write_json(readable / f"{name}.json", rows)
        if rows:
            write_csv(readable / f"{name}.csv", rows, list(rows[0]))
    fields_text = []
    for row in datasets["campos_escritos"]:
        fields_text.append(f"# {row['Referencia']}\n\nLocationId: {row['LocationId']} · TextTag: {row['TextTag']}\n\n{row['Value']}\n")
    (readable / "campos_escritos.md").write_text("\n\n---\n\n".join(fields_text), encoding="utf-8")
    bookmarks_text = [f"# {row['Title']}\n\n{row['Referencia']}\n\nSlot: {row['Slot']}\n\n{row['Snippet'] or ''}\n" for row in datasets["marcadores"]]
    (readable / "marcadores.md").write_text("\n\n---\n\n".join(bookmarks_text), encoding="utf-8")
    tags_index = ["# Índice de etiquetas\n"]
    for tag in table("Tag"):
        tags_index.append(f"\n## {tag['Name']}\n\nID: {tag['TagId']} · Tipo: {tag['Type']}\n")
        for mapping in sorted(table("TagMap"), key=lambda item: item["Position"]):
            if mapping["TagId"] != tag["TagId"]:
                continue
            if mapping["NoteId"] is not None:
                note = next(item for item in notes if item["NoteId"] == mapping["NoteId"])
                label = (note["Title"] or "Sin título").replace("\n", " ").replace("[", "(").replace("]", ")")
                tags_index.append(f"- [{label}]({note['ArchivoMarkdown'].replace(' ', '%20')}) — {note['Referencia']}\n")
            elif mapping["LocationId"] is not None:
                tags_index.append(f"- {reference(locations.get(mapping['LocationId']))} (LocationId: {mapping['LocationId']})\n")
            else:
                tags_index.append(f"- {playlist_items[mapping['PlaylistItemId']]['Label']} (PlaylistItemId: {mapping['PlaylistItemId']})\n")
    (readable / "indice_etiquetas.md").write_text("".join(tags_index), encoding="utf-8")

    media_output = output / "imagenes"
    media_output.mkdir()
    media_inventory = []
    for entry in inventory:
        if Path(entry["archivo"]).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        metadata = next((row for row in media.values() if row["FilePath"] == entry["archivo"]), None)
        filename = f"{metadata['IndependentMediaId']:03d}_{slug(metadata['OriginalFilename'])}" if metadata else slug(entry["archivo"])
        if not Path(filename).suffix:
            filename += Path(entry["archivo"]).suffix
        target = media_output / filename
        shutil.copyfile(raw / entry["archivo"], target)
        media_inventory.append({**entry, "MetadatosBaseDatos": metadata, "ArchivoLegible": filename})
    write_json(media_output / "indice.json", media_inventory)
    gallery = ["# Imágenes extraídas\n"]
    for item in media_inventory:
        filename = item["ArchivoLegible"]
        gallery.append(f"\n## {filename}\n\n![{filename}]({filename.replace(' ', '%20')})\n")
    (media_output / "galeria.md").write_text("".join(gallery), encoding="utf-8")

    ocr_results = []
    if args.ocr and shutil.which("tesseract"):
        ocr_output = output / "ocr_imagenes"
        ocr_output.mkdir()
        language = "eng"
        tessdata = output / "herramientas" / "tessdata"
        tessdata.mkdir(parents=True)
        language_status = ""
        try:
            model = tessdata / "spa.traineddata"
            with urllib.request.urlopen("https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/spa.traineddata", timeout=30) as response:
                model.write_bytes(response.read())
            language = "spa"
            language_status = "Modelo español de tesseract-ocr/tessdata_fast, OCR ejecutado localmente"
        except Exception as error:
            language_status = f"Modelo español no disponible; se utiliza inglés: {error}"
        for item in media_inventory:
            image = media_output / item["ArchivoLegible"]
            command = ["tesseract", str(image), "stdout", "-l", language]
            if language == "spa":
                command.extend(["--tessdata-dir", str(tessdata)])
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=60)
                text_file = ocr_output / (image.name + ".txt")
                text_file.write_text(result.stdout, encoding="utf-8")
                ocr_results.append({"archivo": image.name, "salida": text_file.name, "idioma": language,
                                    "estado": result.returncode, "diagnostico": result.stderr.strip(),
                                    "caracteres": len(result.stdout)})
            except Exception as error:
                ocr_results.append({"archivo": image.name, "error": str(error)})
        write_json(ocr_output / "resultado.json", {"modelo": language_status, "imagenes": ocr_results})

    dates = []
    date_errors = []
    for row in notes:
        try:
            date = datetime.fromisoformat(row["Created"].replace("Z", "+00:00"))
            if date.tzinfo is None:
                raise ValueError("Fecha sin zona horaria")
            dates.append(date.astimezone(timezone.utc))
        except (TypeError, ValueError, AttributeError) as error:
            date_errors.append({"NoteId": row["NoteId"], "Created": row.get("Created"), "error": str(error)})
    summary = {
        "archivo_fuente": str(source), "sha256_fuente": original_hash, "bytes_fuente": source.stat().st_size,
        "manifest": manifest, "tablas": {name: len(rows) for name, rows in tables.items()},
        "imagenes": len(media_inventory), "notas_sin_ubicacion": sum(row["LocationId"] is None for row in notes),
        "notas_con_contenido_vacio": sum(not (row["Content"] or "").strip() for row in notes),
        "campos_escritos_no_vacios": sum(bool(row["Value"].strip()) for row in table("InputField")),
        "etiquetas_por_tipo": dict(Counter(row["Type"] for row in table("Tag"))),
        "colores_por_indice": dict(Counter(row["ColorIndex"] for row in table("UserMark"))),
        "notas_creadas_desde_utc": min(dates).isoformat() if dates else None,
        "notas_creadas_hasta_utc": max(dates).isoformat() if dates else None,
        "fechas_no_interpretadas": date_errors,
        "listas_reproduccion": [{"nombre": row["Name"], "elementos": len(row["Elementos"])} for row in datasets["listas_reproduccion"]],
        "validacion": {"zip_crc": "ok", "sqlite_integrity_check": integrity,
                       "sqlite_foreign_key_check": foreign_keys, "sha256_database": sha256(database.read_bytes()),
                       "hash_manifest": manifest["userDataBackup"]["hash"],
                       "nota_hash_manifest": "El manifiesto no declara el algoritmo; se conserva su valor sin interpretarlo como SHA-256.",
                       "original_sin_cambios": original_hash == sha256(source.read_bytes())},
        "ocr_imagenes": ocr_results,
    }
    write_json(output / "resumen.json", summary)
    report = ["# Extracción de la copia JW Library\n\n",
              f"Archivo: `{source.name}`\n\nCopia creada: {manifest['creationDate']}\n\n",
              f"Última modificación indicada por el manifiesto: {manifest['userDataBackup']['lastModifiedDate']}\n\n",
              f"Dispositivo: {manifest['userDataBackup']['deviceName']} · Versión de esquema: {manifest['userDataBackup']['schemaVersion']}\n\n",
              "## Datos recuperados\n\n",
              f"- {len(notes):,} notas, incluidas {summary['notas_sin_ubicacion']} sin ubicación y {summary['notas_con_contenido_vacio']} con contenido vacío.\n",
              f"- {len(table('UserMark')):,} marcas de subrayado y {len(table('BlockRange')):,} rangos.\n",
              f"- {len(table('Bookmark'))} marcadores.\n",
              f"- {len(table('InputField'))} campos escritos ({summary['campos_escritos_no_vacios']} no vacíos).\n",
              f"- {len(table('Tag'))} etiquetas: {dict(Counter(row['Type'] for row in table('Tag')))} por tipo original.\n",
              f"- {len(datasets['listas_reproduccion'])} listas de reproducción y {len(playlist_items)} elementos.\n",
              f"- {len(datasets['favoritos'])} asociaciones de favoritos.\n",
              f"- {len(media_inventory)} imágenes originales (con la miniatura predeterminada si está incluida).\n",
              f"- {len(locations):,} ubicaciones o referencias.\n\n",
              f"Fechas de creación de las notas: {summary['notas_creadas_desde_utc']} a {summary['notas_creadas_hasta_utc']} (UTC).\n\n",
              "## Archivos para consultar\n\n",
              "- [Todas las notas en Markdown](legible/notas.md) y [texto plano](legible/notas.txt).\n",
              "- [Notas con referencias, etiquetas y rangos en JSON](legible/notas.json) y [CSV](legible/notas.csv).\n",
              "- [Índice por etiquetas](legible/indice_etiquetas.md), con enlaces a cada nota individual.\n",
              "- [Campos escritos](legible/campos_escritos.md) y [marcadores](legible/marcadores.md).\n",
              "- [Subrayados y posiciones](legible/subrayados.json), [listas de reproducción](legible/listas_reproduccion.json) y [favoritos](legible/favoritos.json).\n",
              "- [Galería de imágenes](imagenes/galeria.md) e [índice de nombres y metadatos](imagenes/indice.json).\n",
              "- [Todos los datos en un JSON](datos_completos.json), [volcado SQL](datos_completos.sql) y [esquema SQL](esquema.sql).\n",
              "- Todas las tablas en `tablas/json/` y `tablas/csv/`, incluso las vacías.\n",
              f"- Los {len(inventory)} archivos del contenedor, sin modificar, están en `originales/`.\n\n",
              "## Alcance e interpretación\n\n",
              "El contenido de notas, títulos y campos escritos se conserva completo en las exportaciones JSON y SQL. "
              "En CSV, NULL se representa como celda vacía; JSON y SQL distinguen NULL de una cadena vacía. "
              "Las fechas originales y los identificadores se conservan. Los nombres de libros bíblicos se añaden como ayuda de lectura "
              "según su número; los títulos de publicación solo se muestran cuando existen en el respaldo.\n\n",
              "Los subrayados contienen referencias, índices de color y estilo, identificadores de bloques y posiciones de tokens. "
              "El respaldo no incluye el texto completo de las publicaciones o de las Biblias; no permite reconstruir "
              "todo el texto subrayado. Algunas notas y marcadores sí incluyen texto en su título o fragmento. "
              "Los índices de color y demás códigos se conservan sin asignarles nombres que no figuren en el archivo.\n\n",
              "Esta extracción incluye todos los registros actuales de todas las tablas. No se ha intentado recuperar registros "
              "borrados o interpretar bytes residuales; la base de datos original se conserva para análisis posteriores.\n\n",
              "Las imágenes se conservan byte por byte, incluyendo cualquier metadato incrustado. "
              "La carpeta `imagenes/` añade copias con nombres de origen cuando están disponibles.\n\n",
              "## Validación\n\n",
              f"- CRC de todas las entradas ZIP: correcto.\n- Integridad SQLite: {', '.join(integrity)}.\n",
              f"- Errores de claves foráneas: {len(foreign_keys)}.\n- Archivo original sin cambios: {summary['validacion']['original_sin_cambios']}.\n",
              f"- SHA-256 del archivo original: `{original_hash}`.\n",
              "- El hash del manifiesto se conserva; el algoritmo no se identifica en el manifiesto.\n\n",
              "## Inventario de tablas\n\n| Tabla | Registros |\n|---|---:|\n"]
    report.extend(f"| {name} | {len(rows)} |\n" for name, rows in tables.items())
    if ocr_results:
        report.append("\n## Texto de las imágenes\n\nEl OCR se ha ejecutado localmente. Los textos están en `ocr_imagenes/`; "
                      "pueden contener errores u omisiones, especialmente en mapas, etiquetas pequeñas y diagramas. "
                      "Las imágenes originales son la referencia. [Resultado del OCR](ocr_imagenes/resultado.json).\n")
    (output / "LEEME.md").write_text("".join(report), encoding="utf-8")
    shutil.copyfile(Path(__file__).resolve(), output / "extraer_jwlibrary.py")
    connection.close()
    print(json.dumps({"carpeta": str(output), "tablas": summary["tablas"], "imagenes": summary["imagenes"],
                      "validacion": summary["validacion"], "ocr": len(ocr_results)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
