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

Die App legt außerdem **automatisch einmal täglich** eine Kopie der Datenbank in `daten/sicherungen/` ab und behält die letzten 14.

Zum Sichern kopierst du einfach den Ordner `daten/`, z. B. auf eine externe Platte oder in einen Backup-Ordner. Alternativ:

```bash
python3 app.py backup          # legt sicherung_JJJJ-MM-TT_HH-MM.zip an
python3 app.py export ./export # alles als Markdown + CSV (lesbar mit jedem Editor, Obsidian usw.)
```

Ein Export als ZIP geht auch über **„Export“** in der Seitenleiste.

## Dein FlowBoard

Seiten können Datenbanken **einbetten**. Damit funktioniert ein Dashboard wie das FlowBoard auch lokal: To-do-Board, Daily Tracker und Ziele liegen auf einer Seite und lassen sich direkt dort bearbeiten.

- **Als Startseite festlegen:** FlowBoard öffnen → „•••“ → „Als Startseite festlegen“. Die App startet dann immer dort. Mit ☆ kommt die Seite zusätzlich in die Favoriten.
- **Karten verschieben:** mit der Maus ziehen, am Touchscreen kurz gedrückt halten und dann ziehen. Die Reihenfolge innerhalb einer Spalte bleibt gespeichert.
- **Schnell erfassen:** „＋ Neu“ in einer Spalte, Titel tippen, Enter. Das Feld bleibt für die nächste Karte offen, Esc schließt es.
- **Karte öffnen:** Ein Klick öffnet sie rechts in einer Seitenleiste, das Board bleibt sichtbar. Esc schließt.
- **Spalten anpassen:** „⋯“ im Spaltenkopf zum Umbenennen, Einfärben, Verschieben, Ausblenden (z. B. „Completed“ oder „Hidden Ideas“) oder Löschen.
- **Ansicht anpassen:** „⚙ Ansicht“ legt fest, wonach gruppiert und sortiert wird und welche Eigenschaften auf den Karten stehen.
- **Toggles:** Aufklappbare Bereiche aus Notion bleiben aufklappbar. Ob sie offen oder zu sind, wird gespeichert.

Nach dem Import einmalig prüfen: Notion exportiert die **Reihenfolge und Farben der Board-Spalten** nicht. Bring sie über „⋯“ einmal in die gewünschte Reihenfolge, danach bleibt sie erhalten, auch bei einem erneuten Import. Spalten-Layouts (nebeneinander) exportiert Notion ebenfalls nicht, die Inhalte stehen dann untereinander.

## Bedienung

| Aktion | So geht's |
|---|---|
| Text bearbeiten | auf den Text klicken, der Cursor landet an der Stelle. „Fertig“, Esc oder ein Klick daneben beendet (speichert automatisch) |
| Checkbox abhaken | direkt anklicken, ohne Bearbeiten-Modus |
| Bild einfügen | im Editor einfügen (Strg+V), hineinziehen oder „🖼 Bild/Datei“ |
| Datenbank/Board in eine Seite einbetten | im Editor „🗃️ Datenbank“ |
| Link auf andere Seite | im Editor „🔗 Seite“ |
| Datenbank-Zelle ändern | in die Zelle klicken (Datum mit Kalender, Auswahl mit Liste) |
| Eigenschaft umbenennen, Typ ändern, sortieren | in der Tabelle auf die Spaltenüberschrift klicken |
| Eintrag löschen/duplizieren | Rechtsklick auf Karte oder Zeile. Nach dem Löschen erscheint „Rückgängig“ |
| Seite verschieben | in der Seitenleiste auf eine andere Seite ziehen oder „•••“ → „Verschieben nach“ |
| Suchen | Strg/Cmd + K |
| Hell/Dunkel | „Darstellung“ unten in der Seitenleiste |

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
- Einstellbar per Umgebungsvariablen: `NOTION_LOKAL_PORT`, `NOTION_LOKAL_DATEN` (anderer Datenordner)
- Zugriff vom Handy im eigenen WLAN: mit `NOTION_LOKAL_HOST=0.0.0.0` starten und am Handy `http://<IP-des-Rechners>:8765` öffnen. Achtung: Es gibt kein Passwort, also nur im eigenen Heimnetz verwenden.
