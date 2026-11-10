# Notion Lokal

Eine lokale, Notion-ähnliche Datenbank für deinen eigenen Rechner. Alle Daten bleiben auf deiner Festplatte, es gibt keine Cloud und kein Konto.

- **Seiten** mit Unterseiten, Markdown, Bildern, Callouts, Checklisten und Tabellen
- **Datenbanken** mit Tabellen- und Board-Ansicht (Kanban), Spaltentypen (Auswahl, Mehrfachauswahl, Datum, Checkbox, URL …), Sortieren und Filtern
- **Volltextsuche** (Strg/Cmd + K), Papierkorb, Seiten verschieben per Drag & Drop
- **Import** von Notion-Exporten (Markdown & CSV), auch mehrteilige ZIPs
- **Export** jederzeit zurück nach Markdown + CSV, damit du nie wieder an ein Programm gebunden bist

Du brauchst nur **Python 3.9 oder neuer**. Zusätzliche Pakete sind nicht nötig.

## 1. Daten aus Notion exportieren

1. Öffne in Notion **Einstellungen → Allgemein → „Alle Inhalte des Arbeitsbereichs exportieren“** (englisch: *Settings → General → Export all workspace content*).
2. Wähle:
   - Exportformat: **Markdown & CSV**
   - Inhalt einschließen: **Alles** (inkl. Dateien & Bilder)
   - **Ordner für Unterseiten erstellen**: an
3. Notion schickt dir per E-Mail einen Download-Link. Lade die ZIP-Datei herunter und **entpacke sie nicht**.

> Tipp: Mach zusätzlich einen Export im Format **HTML** und bewahre ihn als reines Lesearchiv auf. Darin sehen Seiten noch genauso aus wie in Notion.

## 2. Starten

- **Mac:** Doppelklick auf `Start (Mac).command`. Beim ersten Mal ggf. Rechtsklick → Öffnen.
- **Windows:** Doppelklick auf `Start (Windows).bat` (Python von python.org installieren, Haken bei „Add to PATH“ setzen).
- **Terminal:** `python3 app.py`

Danach öffnet sich der Browser mit **http://127.0.0.1:8765**. Die App ist nur auf deinem eigenen Rechner erreichbar.

## 3. Importieren

Klicke in der Seitenleiste auf **„Notion importieren“** und ziehe die ZIP-Datei hinein.

Bei sehr großen Exporten (mehrere GB) geht es schneller über das Terminal:

```bash
python3 app.py import ~/Downloads/Export-xxxx.zip
```

Mehrere ZIPs (Notion teilt große Exporte auf) kannst du nacheinander angeben. Importierst du einen neueren Export desselben Arbeitsbereichs, werden die vorhandenen Seiten anhand ihrer Notion-ID **aktualisiert** und nicht verdoppelt. Lokale Änderungen an diesen Seiten werden dabei überschrieben.

## 4. Sichern

Alles liegt im Ordner **`daten/`**:

| Datei | Inhalt |
|---|---|
| `daten/notion.sqlite3` | alle Seiten, Datenbanken und Einträge |
| `daten/dateien/` | Bilder und Anhänge |

Zum Sichern kopierst du einfach den Ordner `daten/`, z. B. auf eine externe Platte oder in einen Backup-Ordner. Alternativ:

```bash
python3 app.py backup          # legt sicherung_JJJJ-MM-TT_HH-MM.zip an
python3 app.py export ./export # alles als Markdown + CSV (lesbar mit jedem Editor, Obsidian usw.)
```

Ein Export als ZIP geht auch über **„Export“** in der Seitenleiste.

## Bedienung

| Aktion | So geht's |
|---|---|
| Text bearbeiten | in den Text klicken, Esc oder Strg+Enter zum Beenden (speichert automatisch) |
| Bild einfügen | im Bearbeiten-Modus einfügen (Strg+V) oder Datei hineinziehen |
| Link auf andere Seite | „•••“ → „Link zu dieser Seite kopieren“, dann einfügen |
| Datenbank-Zelle ändern | in die Zelle klicken |
| Spaltentyp/Name ändern, sortieren | auf die Spaltenüberschrift klicken |
| Eintrag öffnen | „ÖFFNEN“ beim Hovern über den Namen oder Karte im Board anklicken |
| Seite verschieben | in der Seitenleiste auf eine andere Seite ziehen oder „•••“ → „Verschieben nach“ |
| Suchen | Strg/Cmd + K |

## Was beim Import (noch) nicht übernommen wird

Diese Einschränkungen kommen vom Notion-Export selbst:

- **Seiten-Icons und Titelbilder:** Notion exportiert sie im Markdown-Format nicht. Icons kannst du lokal neu setzen.
- **Gespeicherte Ansichten, Filter, Formeln und Rollups:** Formeln und Rollups kommen als fester Text-Wert an.
- **Verknüpfungen (Relations)** werden als klickbare Links übernommen, sind aber keine „echten“ Relationen mehr.
- **Spaltentypen** werden aus den Werten erraten. Ein Klick auf die Spaltenüberschrift korrigiert sie.
- **Kommentare und Versionsverlauf** sind im Export nicht enthalten.

## Technik

- `app.py`: Python-Server (nur Standardbibliothek), SQLite-Datenbank, Import und Export
- `static/index.html`: komplette Oberfläche ohne externe Bibliotheken, funktioniert offline
- Einstellbar per Umgebungsvariablen: `NOTION_LOKAL_PORT`, `NOTION_LOKAL_DATEN` (anderer Datenordner, z. B. in der Dropbox)
