# Notion Lokal

Eine lokale, Notion-ähnliche Datenbank für deinen eigenen Rechner. Alle Daten bleiben auf deiner Festplatte, es gibt keine Cloud und kein Konto.

- **Seiten** mit Unterseiten, Markdown, Bildern, Callouts, Checklisten und Tabellen
- **Datenbanken** mit Board, Tabelle, Zeitleiste, Kalender, Liste und Galerie, gespeicherten Ansichten mit Filtern, Summen und Euro-Format, Vorlagen und Cover
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
| `daten/sicherungen/` | automatische tägliche Kopien der Datenbank |
| `daten/sperre.json` | zeigt an, auf welchem Rechner die App gerade läuft |

Die App legt außerdem **automatisch einmal täglich** eine Kopie der Datenbank in `daten/sicherungen/` ab und behält die letzten 14.

Zum Sichern kopierst du einfach den Ordner `daten/`, z. B. auf eine externe Platte oder in einen Backup-Ordner. Alternativ:

```bash
python3 app.py backup          # legt sicherung_JJJJ-MM-TT_HH-MM.zip an
python3 app.py export ./export # alles als Markdown + CSV (lesbar mit jedem Editor, Obsidian usw.)
```

Ein Export als ZIP geht auch über **„Export“** in der Seitenleiste.

## Updates

Die App prüft beim Öffnen, ob im GitHub-Repository eine neuere Version liegt. Dann erscheint unten in der Seitenleiste **„⬆️ Update … installieren“**. Ein Klick lädt die neuen Programmdateien, ersetzt die alten und startet die App neu. Manuell prüfst du mit „⟳ Nach Updates suchen“ oder im Terminal mit `python3 app.py update`.

- Der Ordner `daten/` wird dabei nie angefasst.
- Die vorherigen Programmdateien liegen danach in `alte-version/`. Läuft nach einem Update etwas nicht, kopierst du sie zurück.
- Die App lädt nur von `raw.githubusercontent.com` aus dem eigenen Repository. Es werden keine Daten von dir übertragen.

## An PC und Mac über Dropbox nutzen

Liegt der Ordner `notion-lokal` in der Dropbox, kannst du an mehreren Rechnern mit denselben Daten arbeiten, aber immer nur **abwechselnd**:

1. App am ersten Rechner beenden (Terminalfenster schließen).
2. Warten, bis Dropbox an beiden Rechnern „Aktuell“ zeigt.
3. App am zweiten Rechner starten.

Die App sichert das ab:

- **Sperre:** Ist die App noch an einem anderen Rechner offen, zeigt sie oben einen gelben Hinweis und speichert nichts, bis du dort beendet hast oder bewusst „Trotzdem hier weiterarbeiten“ wählst. Eine Sperre gilt nach 10 Minuten ohne Lebenszeichen als verwaist (z. B. Laptop zugeklappt).
- **Eine Datei:** Die Datenbank ist eine einzige Datei (`daten/notion.sqlite3`), damit Dropbox keinen halben Stand überträgt.
- **Konfliktwarnung:** Legt Dropbox eine „In Konflikt stehende Kopie“ im Ordner `daten/` an, weist die App darauf hin.

Die Sperre erkennt den anderen Rechner erst, wenn Dropbox die Datei `daten/sperre.json` übertragen hat. Schritt 2 bleibt deshalb wichtig.

**Mac:** Meldet der Doppelklick auf `Start (Mac).command` fehlende Zugriffsrechte, einmalig im Terminal `chmod +x ` eintippen, die Datei ins Terminalfenster ziehen und Enter drücken.

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

## Ansichten einer Datenbank

Jede Datenbank hat **gespeicherte Ansichten**, die als Reiter über ihr stehen. Jede Ansicht merkt sich Layout, Filter, Sortierung und Anzeige. Welche Ansicht gerade offen ist, wird pro Stelle gespeichert, an der die Datenbank eingebettet ist.

- **Neue Ansicht:** „+“ rechts neben den Reitern.
- **Umbenennen, Layout wechseln, duplizieren, löschen:** auf den aktiven Reiter klicken. In eingebetteten Datenbanken lässt sich dort auch festlegen, dass an dieser Stelle nur diese eine Ansicht erscheint.
- **Filter:** „Ansicht“ → „+ Filter hinzufügen“, z. B. „Status ist eines von To-do, In Arbeit“ oder „Datum diese Woche“. Mehrere Filter müssen alle zutreffen. Ein Punkt am Reiter zeigt, dass Filter aktiv sind.

| Ansicht | Wofür |
|---|---|
| **Board** | Karten in Spalten nach Status, per Ziehen verschieben |
| **Tabelle** | alle Eigenschaften nebeneinander, direkt in den Zellen bearbeiten |
| **Zeitleiste** | Einträge als Balken über die Zeit. Umschalten zwischen Monat, Quartal und Jahr. Balken ziehen verschiebt den Termin, der rechte Rand ändert das Ende |
| **Kalender** | Monatsraster. „+“ an einem Tag legt dort einen Eintrag an, Einträge lassen sich auf einen anderen Tag ziehen |
| **Liste** | kompakte Zeilen zum schnellen Überfliegen |
| **Galerie** | Karten mit Bild: das Cover des Eintrags oder das erste Bild im Inhalt. Kartengröße unter „Ansicht“ |

Zeitleiste und Kalender nutzen eine Eigenschaft vom Typ „Datum“. Gibt es mehrere, wählst du sie unter „Ansicht“.

**Automatisches Datum:** Neue Einträge bekommen das heutige Datum. Unter „Ansicht“ → „Datum bei neuen Einträgen“ stellst du das pro Datenbank um oder ab.

## Zahlen, Euro und Summen

- **Zahlenformat:** in der Tabelle auf die Überschrift einer Zahlen-Spalte klicken → „Zahlenformat“: Zahl (1.234,5), Euro (1.234,50 €) oder Prozent. Eingaben wie `1234,5`, `1.234,50 €` oder `1234.5` werden erkannt.
- **Berechnungen:** Unter jeder Tabellenspalte erscheint beim Darüberfahren „Berechnen“: Summe, Durchschnitt, Minimum, Maximum, Anzahl, % ausgefüllt, bei Checkboxen % angehakt. Gerechnet wird mit den Einträgen, die Filter und Suche gerade zeigen.

## Vorlagen

Jede Datenbank kann Vorlagen für neue Einträge haben, z. B. „Bug-Report“ oder „Meeting-Notiz“.

- **Vorlage anlegen:** Pfeil „▾“ neben „Neu“ → „+ Neue Vorlage“. Sie öffnet sich rechts. Icon, Cover, Eigenschaften und Inhalt so einstellen, wie neue Einträge aussehen sollen.
- **Eintrag aus Vorlage:** „▾“ → Vorlage anklicken. Bilder der Vorlage werden mitkopiert.
- **Standardvorlage:** „▾“ → „⋯“ neben der Vorlage → „Als Standard festlegen“. Dann nutzen auch „Neu“, die Schnelleingabe im Board und „+“ im Kalender diese Vorlage.
- **Heutiges Datum:** `@heute` in einer Eigenschaft oder im Text wird beim Anlegen durch das aktuelle Datum ersetzt. In Datumsfeldern einer Vorlage gibt es dafür den Punkt „@heute – Tag des Anlegens“.
- Vorlagen erscheinen nicht in den Ansichten, in der Suche oder im Markdown-Export. Sie liegen aber in `daten/notion.sqlite3` und damit in jeder Sicherung.

## Cover

Über dem Titel jeder Seite und jedes Eintrags steht „Cover hinzufügen“: einen Farbverlauf wählen, ein Bild hochladen oder einen Bild-Link einfügen. Fährst du über das Cover, erscheinen „Cover ändern“ und „Entfernen“. Die Galerie zeigt das Cover als Kartenbild.

## Passwortschutz

Unter **„Passwort“** in der Seitenleiste legst du ein Passwort fest (mindestens 6 Zeichen). Danach zeigt die App Seiten, Datenbanken, Suche, Dateien und Export erst nach der Anmeldung.

- Die Anmeldung gilt 30 Tage pro Browser. „Passwort“ → „Abmelden“ beendet sie sofort.
- Das Passwort liegt im Datenordner und gilt damit an PC und Mac gleichermaßen. Gespeichert wird nur ein Prüfwert, nicht das Passwort selbst.
- Ein neues Passwort meldet alle anderen Browser ab.
- Nach 8 Fehlversuchen ist die Anmeldung für ein paar Minuten gesperrt.
- **Passwort vergessen:** Im Terminal im Programmordner `python3 app.py passwort-entfernen` ausführen (Windows: `py app.py passwort-entfernen`).

**Grenzen:** Das Passwort schützt den Zugriff über die App, z. B. wenn jemand anderes deinen Rechner benutzt oder die App im WLAN erreichbar ist. Die Datei `daten/notion.sqlite3` und der Ordner `daten/dateien/` werden **nicht verschlüsselt**. Wer den Datenordner öffnen kann, kann die Inhalte auch ohne Passwort lesen. Dagegen hilft nur eine verschlüsselte Festplatte (BitLocker, FileVault).

## Bedienung

| Aktion | So geht's |
|---|---|
| Text schreiben | einfach in den Text klicken und tippen. Überschriften, Listen, Fett und Farben sind sofort formatiert zu sehen. Esc oder ein Klick daneben beendet (speichert automatisch) |
| Neuer Absatz / Listenpunkt | Enter. In einer Liste beendet ein zweites Enter die Liste. Umschalt+Enter macht einen Zeilenumbruch |
| Blöcke einfügen | `/` tippen: Überschriften, To-dos, Toggles, Spalten, Board, Tabelle, Bild, Video, Seitenlink … Mit Tippen filtern, Enter fügt ein |
| Kurzbefehle | am Zeilenanfang `# ` `## ` `### ` für Überschriften, `- ` für Listen, `[] ` für To-dos, `> ` für Zitate, `1. ` für nummerierte Listen |
| Text formatieren | Text markieren: Es erscheint eine Leiste für fett, kursiv, unterstrichen, Link, Textfarbe, Hintergrundfarbe und Blocktyp. Strg/Cmd+B, I, U gehen auch |
| Tabellen, Code, ganzer Rohtext | unter dem Text „Als Markdown bearbeiten“ |
| Checkbox abhaken | direkt anklicken |
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
- **Gespeicherte Ansichten, Filter und Vorlagen** aus Notion. Lege sie lokal einmal neu an, danach bleiben sie erhalten.
- **Formeln und Rollups** kommen als fester Text-Wert an.
- **Verknüpfungen (Relations)** werden als klickbare Links übernommen, sind aber keine „echten“ Relationen mehr.
- **Spaltentypen** werden aus den Werten erraten. Ein Klick auf die Spaltenüberschrift korrigiert sie.
- **Kommentare und Versionsverlauf** sind im Export nicht enthalten.

## Technik

- `app.py`: Python-Server (nur Standardbibliothek), SQLite-Datenbank, Import und Export
- `static/index.html`: komplette Oberfläche ohne externe Bibliotheken, funktioniert offline
- Einstellbar per Umgebungsvariablen: `NOTION_LOKAL_PORT`, `NOTION_LOKAL_DATEN` (anderer Datenordner)
- Zugriff vom Handy im eigenen WLAN: mit `NOTION_LOKAL_HOST=0.0.0.0` starten und am Handy `http://<IP-des-Rechners>:8765` öffnen. Richte dafür vorher den Passwortschutz ein (siehe oben) und nutze das nur im eigenen Heimnetz: Die Verbindung ist nicht verschlüsselt.
