"""Pruebas de integración con copias sintéticas, sin datos de usuarios."""
import base64
import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "extract_jwlibrary.py"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
CONTENT = "Primera línea: España, Jehová y café.\n\nSegunda línea con 🗺️ y comillas \"literales\"."
SPECIAL = "Texto con CR\r, CRLF\r\n, NUL\x00 y 'comillas'."

SCHEMA = """
CREATE TABLE Location (LocationId INTEGER PRIMARY KEY, BookNumber INTEGER, ChapterNumber INTEGER,
DocumentId INTEGER, Track INTEGER, IssueTagNumber INTEGER, KeySymbol TEXT, MepsLanguage INTEGER,
Type INTEGER, Title TEXT, Specialty TEXT, Edition TEXT);
CREATE TABLE Note (NoteId INTEGER PRIMARY KEY, Guid TEXT, UserMarkId INTEGER, LocationId INTEGER,
Title TEXT, Content TEXT, LastModified TEXT, Created TEXT, BlockType INTEGER, BlockIdentifier INTEGER);
CREATE TABLE UserMark (UserMarkId INTEGER PRIMARY KEY, ColorIndex INTEGER, LocationId INTEGER,
StyleIndex INTEGER, UserMarkGuid TEXT, Version INTEGER);
CREATE TABLE BlockRange (BlockRangeId INTEGER PRIMARY KEY, BlockType INTEGER, Identifier INTEGER,
StartToken INTEGER, EndToken INTEGER, UserMarkId INTEGER);
CREATE TABLE Tag (TagId INTEGER PRIMARY KEY, Type INTEGER, Name TEXT);
CREATE TABLE TagMap (TagMapId INTEGER PRIMARY KEY, PlaylistItemId INTEGER, LocationId INTEGER,
NoteId INTEGER, TagId INTEGER, Position INTEGER);
CREATE TABLE InputField (LocationId INTEGER, TextTag TEXT, Value TEXT, PRIMARY KEY(LocationId, TextTag));
CREATE TABLE Bookmark (BookmarkId INTEGER PRIMARY KEY, LocationId INTEGER, PublicationLocationId INTEGER,
Slot INTEGER, Title TEXT, Snippet TEXT, BlockType INTEGER, BlockIdentifier INTEGER);
CREATE TABLE IndependentMedia (IndependentMediaId INTEGER PRIMARY KEY, OriginalFilename TEXT,
FilePath TEXT, MimeType TEXT, Hash TEXT);
CREATE TABLE PlaylistItem (PlaylistItemId INTEGER PRIMARY KEY, Label TEXT, StartTrimOffsetTicks INTEGER,
EndTrimOffsetTicks INTEGER, Accuracy INTEGER, EndAction INTEGER, ThumbnailFilePath TEXT);
CREATE TABLE PlaylistItemIndependentMediaMap (PlaylistItemId INTEGER, IndependentMediaId INTEGER,
DurationTicks INTEGER, PRIMARY KEY(PlaylistItemId, IndependentMediaId));
CREATE TABLE PlaylistItemLocationMap (PlaylistItemId INTEGER, LocationId INTEGER,
MajorMultimediaType INTEGER, BaseDurationTicks INTEGER, PRIMARY KEY(PlaylistItemId, LocationId));
CREATE TABLE PlaylistItemMarker (PlaylistItemMarkerId INTEGER PRIMARY KEY, PlaylistItemId INTEGER,
Label TEXT, StartTimeTicks INTEGER, DurationTicks INTEGER, EndTransitionDurationTicks INTEGER);
CREATE TABLE FutureTable (Id INTEGER PRIMARY KEY, Payload BLOB, Description TEXT);
"""


def fixture(root, empty=False, minimal=False):
    database = root / "fixture.db"
    connection = sqlite3.connect(database)
    connection.executescript(SCHEMA)
    if minimal:
        for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if name not in {"Note", "Location"}:
                connection.execute(f'DROP TABLE "{name}"')
    if not empty:
        connection.execute("INSERT INTO Location VALUES (1,43,3,NULL,NULL,0,'nwt',1,0,NULL,NULL,NULL)")
        connection.execute("INSERT INTO Location VALUES (2,NULL,NULL,12345,NULL,20240100,'w',1,0,'Publicación de prueba',NULL,NULL)")
        connection.execute("INSERT INTO Note VALUES (1,'guid-1',10,1,'Título: áéí / prueba',?,"
                           "'2024-01-03T10:00:00Z','2024-01-02T12:00:00+02:00',2,16)", (CONTENT,))
        connection.execute("INSERT INTO Note VALUES (2,'guid-2',NULL,NULL,NULL,'',"
                           "'2024-01-04T10:00:00Z','2024-01-03T10:00:00Z',0,NULL)")
        connection.execute("INSERT INTO UserMark VALUES (10,3,1,0,'mark-10',1)")
        connection.execute("INSERT INTO BlockRange VALUES (1,2,16,2,8,10)")
        connection.executemany("INSERT INTO Tag VALUES (?,?,?)", [(1,0,'Favorite'), (2,1,'Ánimo'), (3,2,'Diagramas de prueba')])
        connection.executemany("INSERT INTO TagMap VALUES (?,?,?,?,?,?)",
                               [(1,None,None,1,2,0), (2,None,2,None,1,0), (3,1,None,None,3,0)])
        connection.executemany("INSERT INTO InputField VALUES (?,?,?)", [(2,'tt1',CONTENT), (2,'tt2','')])
        connection.execute("INSERT INTO Bookmark VALUES (1,1,2,0,'Juan 3:16','Fragmento de prueba',2,16)")
        connection.execute("INSERT INTO IndependentMedia VALUES (1,'Diagrama sintético','picture.png','image/png',?)",
                           (hashlib.sha256(PNG).hexdigest(),))
        connection.execute("INSERT INTO PlaylistItem VALUES (1,'Diagrama de prueba',NULL,NULL,1,2,'picture.png')")
        connection.execute("INSERT INTO PlaylistItemIndependentMediaMap VALUES (1,1,40000000)")
        connection.execute("INSERT INTO FutureTable VALUES (1,?,'Tabla desconocida')", (bytes([0,1,255]),))
        connection.execute("INSERT INTO FutureTable VALUES (2,NULL,?)", (SPECIAL,))
    connection.commit()
    expected = {}
    connection.row_factory = sqlite3.Row
    for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        expected[row["name"]] = [dict(item) for item in connection.execute(f'SELECT * FROM "{row["name"]}"')]
    connection.close()
    data = database.read_bytes()
    manifest = {"version": 1, "creationDate": "2024-01-05T12:00:00+0100", "type": 0, "name": "fixture",
                "userDataBackup": {"schemaVersion":16, "lastModifiedDate":"2024-01-04T10:00:00Z",
                                   "hash":hashlib.sha256(data).hexdigest(), "deviceName":"Dispositivo sintético",
                                   "databaseName":"userData.db"}}
    archive = root / "fixture.jwlibrary"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as stream:
        stream.writestr("manifest.json", json.dumps(manifest))
        stream.writestr("userData.db", data)
        if not empty:
            stream.writestr("picture.png", PNG)
    return archive, expected


class ExtractionTests(unittest.TestCase):
    def run_script(self, archive, output):
        return subprocess.run([sys.executable, str(SCRIPT), str(archive), "--output", str(output)],
                              text=True, capture_output=True, timeout=30)

    def test_complete_backup_is_readable_and_lossless(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, expected = fixture(root)
            original = archive.read_bytes()
            output = root / "extraction"
            result = self.run_script(archive, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(archive.read_bytes(), original)
            notes = json.loads((output / "legible/notas.json").read_text())
            self.assertEqual(notes[0]["Content"], CONTENT)
            self.assertEqual(notes[0]["Referencia"], "Juan 3:16")
            self.assertEqual(notes[0]["Etiquetas"][0]["Tag"]["Name"], "Ánimo")
            self.assertEqual(notes[0]["Rangos"][0]["StartToken"], 2)
            self.assertIsNone(notes[1]["Title"])
            self.assertEqual(notes[1]["Content"], "")
            self.assertIn(CONTENT, (output / "legible/notas.md").read_text())
            self.assertIn(CONTENT, (output / "legible/notas.txt").read_text())
            individual = output / "legible" / notes[0]["ArchivoMarkdown"]
            self.assertTrue(individual.is_file())
            self.assertIn(CONTENT, individual.read_text())
            with (output / "tablas/csv/Note.csv").open(encoding="utf-8-sig", newline="") as stream:
                self.assertEqual(next(csv.DictReader(stream))["Content"], CONTENT)
            playlists = json.loads((output / "legible/listas_reproduccion.json").read_text())
            self.assertEqual(playlists[0]["Elementos"][0]["Elemento"]["Medios"][0]["DurationTicks"], 40000000)
            self.assertEqual((output / "originales/picture.png").read_bytes(), PNG)
            summary = json.loads((output / "resumen.json").read_text())
            self.assertEqual(summary["notas_creadas_desde_utc"], "2024-01-02T10:00:00+00:00")
            self.assertEqual(summary["campos_escritos_no_vacios"], 1)
            self.assertEqual(summary["validacion"]["sqlite_integrity_check"], ["ok"])
            with zipfile.ZipFile(archive) as stream:
                for name in stream.namelist():
                    self.assertEqual((output / "originales" / name).read_bytes(), stream.read(name))
            restored = sqlite3.connect(":memory:")
            restored.row_factory = sqlite3.Row
            restored.executescript((output / "datos_completos.sql").read_text())
            exported = json.loads((output / "datos_completos.json").read_text())["tablas"]
            for name, rows in expected.items():
                self.assertEqual([dict(row) for row in restored.execute(f'SELECT * FROM "{name}"')], rows)
                decoded = [{key: base64.b64decode(value["base64"]) if isinstance(value,dict) and value.get("__type__") == "bytes"
                            else value for key,value in row.items()} for row in exported[name]]
                self.assertEqual(decoded, rows)
                self.assertTrue((output / "tablas/csv" / (name + ".csv")).exists())
            restored.close()

    def test_empty_backup_without_optional_tables(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, _ = fixture(root, empty=True, minimal=True)
            output = root / "out"
            result = self.run_script(archive, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((output / "resumen.json").read_text())
            self.assertIsNone(summary["notas_creadas_desde_utc"])
            self.assertEqual(json.loads((output / "legible/notas.json").read_text()), [])
            self.assertEqual(summary["tablas"], {"Location":0, "Note":0})

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, _ = fixture(root, empty=True)
            output = root / "out"
            output.mkdir()
            (output / "sentinel.txt").write_text("conservar")
            result = self.run_script(archive, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((output / "sentinel.txt").read_text(), "conservar")
            self.assertEqual(list(output.iterdir()), [output / "sentinel.txt"])

    def test_archive_paths_cannot_escape_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "bad.jwlibrary"
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("../../escape.txt", "contenido")
            result = self.run_script(archive, root / "out")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "escape.txt").exists())
            self.assertIn("Ruta no permitida", result.stderr)

    def test_invalid_note_date_does_not_lose_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive, _ = fixture(root)
            database = root / "fixture.db"
            with sqlite3.connect(database) as connection:
                connection.execute("UPDATE Note SET Created='fecha desconocida' WHERE NoteId=1")
            with zipfile.ZipFile(archive) as stream:
                manifest = stream.read("manifest.json")
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr("manifest.json", manifest)
                stream.writestr("userData.db", database.read_bytes())
                stream.writestr("picture.png", PNG)
            output = root / "out"
            result = self.run_script(archive, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads((output / "resumen.json").read_text())
            self.assertEqual(summary["fechas_no_interpretadas"][0]["NoteId"], 1)
            self.assertIn(CONTENT, (output / "legible/notas.md").read_text())


if __name__ == "__main__":
    unittest.main()
