#!/usr/bin/env python3
"""
Notion Lokal – eine lokale, Notion-ähnliche Datenbank.

* Keine Installation nötig: nur Python 3.9+ (Standardbibliothek).
* Alle Daten liegen lokal im Ordner ./daten (SQLite-Datei + Dateianhänge).
* Importiert Notion-Exporte im Format "Markdown & CSV" (ZIP oder entpackter Ordner).

Benutzung:
    python3 app.py                      # Server starten -> http://127.0.0.1:8765
    python3 app.py import Export.zip    # Notion-Export importieren
    python3 app.py export ./ordner      # Alles als Markdown + CSV exportieren
    python3 app.py backup               # ZIP-Sicherung des Datenordners anlegen
    python3 app.py update               # neueste Version von GitHub einspielen (--trotzdem: lokale Änderungen überschreiben)
    python3 app.py passwort-entfernen   # Passwortschutz aufheben (Passwort vergessen)
"""

import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import mimetypes
import os
import platform
import signal
import subprocess
import urllib.request
import time
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import threading
import urllib.parse
import uuid
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("NOTION_LOKAL_DATEN", os.path.join(BASE_DIR, "daten"))
DB_PATH = os.path.join(DATA_DIR, "notion.sqlite3")
FILES_DIR = os.path.join(DATA_DIR, "dateien")
STATIC_DIR = os.path.join(BASE_DIR, "static")
HOST = os.environ.get("NOTION_LOKAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("NOTION_LOKAL_PORT", "8765"))

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))

# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pages (
    id        TEXT PRIMARY KEY,
    parent_id TEXT,
    kind      TEXT NOT NULL DEFAULT 'page',   -- page | database | row | template
    title     TEXT NOT NULL DEFAULT '',
    icon      TEXT NOT NULL DEFAULT '',
    content   TEXT NOT NULL DEFAULT '',       -- Markdown
    props     TEXT NOT NULL DEFAULT '{}',     -- JSON (nur bei kind=row)
    schema    TEXT NOT NULL DEFAULT '[]',     -- JSON (nur bei kind=database)
    sort      REAL NOT NULL DEFAULT 0,
    created   TEXT NOT NULL,
    updated   TEXT NOT NULL,
    deleted   INTEGER NOT NULL DEFAULT 0,
    source    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pages_parent ON pages(parent_id);
CREATE INDEX IF NOT EXISTS idx_pages_kind ON pages(kind);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS versions (           -- Seitenverlauf: frühere Fassungen einer Seite
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    page_id TEXT NOT NULL,
    ts      TEXT NOT NULL,
    title   TEXT NOT NULL DEFAULT '',
    icon    TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    props   TEXT NOT NULL DEFAULT '{}',
    reason  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_versions_page ON versions(page_id, id);
"""

_write_lock = threading.Lock()

HISTORY_GAP_S = 600    # höchstens alle 10 Minuten eine neue Fassung je Seite (Autospeichern)
HISTORY_KEEP = 50      # so viele Fassungen bleiben je Seite erhalten


def snapshot(con, pid, change=None, reason="", force=False):
    """Den aktuellen Stand einer Seite als Fassung ablegen, bevor er überschrieben wird.

    change: die geplante Änderung (dict). Nur wenn sie Titel, Icon, Inhalt oder
    Eigenschaften wirklich ändert, wird gesichert. Ohne force höchstens alle
    HISTORY_GAP_S Sekunden, damit Autospeichern nicht hunderte Fassungen erzeugt."""
    r = con.execute("SELECT kind,title,icon,content,props FROM pages WHERE id=?", (pid,)).fetchone()
    if not r:
        return False
    cur = {"title": r["title"], "icon": r["icon"], "content": r["content"],
           "props": json.loads(r["props"] or "{}")}
    if change is not None and not any(k in change and change[k] != cur[k] for k in cur):
        return False
    if not (cur["title"] or cur["content"].strip() or any(str(v).strip() for v in cur["props"].values())):
        return False                     # leere, gerade angelegte Seite
    last = con.execute("SELECT ts,title,icon,content,props FROM versions WHERE page_id=? ORDER BY id DESC LIMIT 1",
                       (pid,)).fetchone()
    if last:
        same = (last["title"], last["icon"], last["content"], json.loads(last["props"] or "{}")) == \
            (cur["title"], cur["icon"], cur["content"], cur["props"])
        if same:
            return False
        if not force:
            try:
                age = (dt.datetime.now() - dt.datetime.fromisoformat(last["ts"])).total_seconds()
            except ValueError:
                age = HISTORY_GAP_S
            if 0 <= age < HISTORY_GAP_S:
                return False
    con.execute("INSERT INTO versions (page_id,ts,title,icon,content,props,reason) VALUES (?,?,?,?,?,?,?)",
                (pid, now(), cur["title"], cur["icon"], cur["content"],
                 json.dumps(cur["props"], ensure_ascii=False), reason))
    con.execute("DELETE FROM versions WHERE page_id=? AND id NOT IN "
                "(SELECT id FROM versions WHERE page_id=? ORDER BY id DESC LIMIT ?)", (pid, pid, HISTORY_KEEP))
    return True


# ---------------------------------------------------------------------------
# Sperre für den Betrieb an mehreren Rechnern (Datenordner in Dropbox)
# ---------------------------------------------------------------------------

LOCK_PATH = os.path.join(DATA_DIR, "sperre.json")
INSTANCE = {"id": uuid.uuid4().hex,
            "host": os.environ.get("NOTION_LOKAL_RECHNERNAME") or platform.node() or "Unbekannter Rechner"}
HEARTBEAT_S = 180      # so oft meldet sich die laufende App in der Sperrdatei
STALE_S = 600          # danach gilt eine Sperre als verwaist (Rechner zugeklappt, abgestürzt)
OWNS_LOCK = False
CONFLICT_RE = re.compile(r"conflicted copy|in konflikt|konfliktkopie|conflict", re.I)


def read_lock():
    try:
        with open(LOCK_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def take_lock():
    global OWNS_LOCK
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOCK_PATH, "w", encoding="utf-8") as fh:
        json.dump({**INSTANCE, "time": time.time()}, fh)
    OWNS_LOCK = True


def release_lock():
    global OWNS_LOCK
    cur = read_lock()
    if OWNS_LOCK and cur and cur.get("id") == INSTANCE["id"]:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
    OWNS_LOCK = False


def foreign_lock():
    """Sperre eines anderen Rechners (oder None). Aktualisiert OWNS_LOCK, falls übernommen wurde."""
    global OWNS_LOCK
    cur = read_lock()
    if not cur or cur.get("id") == INSTANCE["id"]:
        return None
    OWNS_LOCK = False
    age = max(0, int(time.time() - float(cur.get("time", 0))))
    return {"host": cur.get("host", "?"), "age": age, "stale": age > STALE_S}


def acquire_lock_on_start():
    other = foreign_lock()
    if other is None or other["stale"] or other["host"] == INSTANCE["host"]:
        take_lock()
        return None
    return other


def _heartbeat_loop():
    if OWNS_LOCK and foreign_lock() is None:
        take_lock()
    t = threading.Timer(HEARTBEAT_S, _heartbeat_loop)
    t.daemon = True
    t.start()


def conflict_files():
    try:
        return sorted(f for f in os.listdir(DATA_DIR) if CONFLICT_RE.search(f))
    except OSError:
        return []


def lock_status():
    other = foreign_lock()
    return {"owns": OWNS_LOCK and other is None, "host": INSTANCE["host"], "other": other,
            "conflicts": conflict_files()}


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    # Eine einzige Datei ohne -wal/-shm: wichtig, wenn der Datenordner in Dropbox o. Ä. liegt
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA_SQL)
    _migrate(con)
    return con


_migrated = False


def _migrate(con):
    """Spalten nachrüsten, die es in älteren Datenbanken noch nicht gibt."""
    global _migrated
    if _migrated:
        return
    cols = {r[1] for r in con.execute("PRAGMA table_info(pages)")}
    if "cover" not in cols:
        con.execute("ALTER TABLE pages ADD COLUMN cover TEXT NOT NULL DEFAULT ''")
        con.commit()
    _migrated = True


def new_id():
    return uuid.uuid4().hex


def row_to_page(r, full=True):
    d = {
        "id": r["id"],
        "parent_id": r["parent_id"],
        "kind": r["kind"],
        "title": r["title"],
        "icon": r["icon"],
        "cover": r["cover"] if "cover" in r.keys() else "",
        "sort": r["sort"],
        "created": r["created"],
        "updated": r["updated"],
        "deleted": bool(r["deleted"]),
    }
    if full:
        d["content"] = r["content"]
        d["props"] = json.loads(r["props"] or "{}")
        d["schema"] = json.loads(r["schema"] or "[]")
    return d


# ---------------------------------------------------------------------------
# Vorlagen und wiederkehrende Einträge
# ---------------------------------------------------------------------------

def meta_get(con, key, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    try:
        return json.loads(r["value"]) if r else default
    except ValueError:
        return default


def meta_set(con, key, value):
    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)))


def create_from_template(con, tid, title="", extra=None, day=None, date_col=None):
    """Eintrag aus einer Vorlage anlegen (ohne commit). day: Datum für „@heute“ und die Datumsspalte."""
    t = con.execute("SELECT * FROM pages WHERE id=? AND kind='template' AND deleted=0", (tid,)).fetchone()
    if not t:
        return None
    db = con.execute("SELECT schema FROM pages WHERE id=?", (t["parent_id"],)).fetchone()
    schema = json.loads(db["schema"]) if db else []
    tcol = next((c["name"] for c in schema if c["type"] == "title"), None)
    nid, ts = new_id(), now()
    day = day or dt.date.today()
    props = json.loads(t["props"] or "{}")
    for k, v in list(props.items()):
        if isinstance(v, str) and "@heute" in v:
            props[k] = v.replace("@heute", day.isoformat())
    props.update(extra or {})
    if date_col is not None:
        dcol = next((c["name"] for c in schema if c["type"] == "date" and c["name"] == date_col), None) \
            or next((c["name"] for c in schema if c["type"] == "date"), None)
        if dcol and not str(props.get(dcol, "")).strip():
            props[dcol] = day.isoformat()
    if tcol:
        props[tcol] = title
    swap = lambda x: (x or "").replace(f"/dateien/{tid}/", f"/dateien/{nid}/")  # noqa: E731
    content = swap(t["content"]).replace("@heute", day.strftime("%d.%m.%Y"))
    mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM pages WHERE parent_id IS ? AND kind='row'",
                     (t["parent_id"],)).fetchone()[0]
    con.execute(
        "INSERT INTO pages (id,parent_id,kind,title,icon,cover,content,props,schema,sort,created,updated) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (nid, t["parent_id"], "row", title, t["icon"], swap(t["cover"]), content,
         json.dumps(props, ensure_ascii=False), "[]", mx, ts, ts))
    src = os.path.join(FILES_DIR, tid)
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(FILES_DIR, nid), dirs_exist_ok=True)
    m = re.search(r"!\[[^\]]*\]\(([^)\s]+)", content)
    return {"id": nid, "title": title, "icon": t["icon"], "cover": swap(t["cover"]), "props": props,
            "sort": mx, "has_content": bool(content.strip()), "thumb": m.group(1) if m else "",
            "db": t["parent_id"], "template_title": t["title"]}


WEEKDAYS_DE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def recur_due(cfg, today):
    """Letzter fälliger Tag (<= heute) einer Wiederholung oder None.

    cfg: {"freq": "daily"|"weekly"|"monthly", "days": [1..7] (Mo=1), "mday": 1..31, "since": "JJJJ-MM-TT"}"""
    try:
        since = dt.date.fromisoformat(cfg.get("since") or today.isoformat())
    except ValueError:
        since = today
    freq, d = cfg.get("freq"), None
    if freq == "daily":
        d = today
    elif freq == "weekly":
        days = {int(x) for x in (cfg.get("days") or []) if str(x).isdigit()} or {since.isoweekday()}
        d = next((today - dt.timedelta(k) for k in range(7) if (today - dt.timedelta(k)).isoweekday() in days), None)
    elif freq == "monthly":
        want = max(1, min(31, int(cfg.get("mday") or 1)))

        def in_month(y, m):
            last = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(1)).day
            return dt.date(y, m, min(want, last))
        d = in_month(today.year, today.month)
        if d > today:
            y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
            d = in_month(y, m)
    if d is None or d < since:
        return None
    return d


def recur_title(tpl_title, day):
    date = day.strftime("%d.%m.%Y")
    tpl_title = (tpl_title or "").strip()
    if "@heute" in tpl_title:
        return tpl_title.replace("@heute", date)
    return f"{tpl_title} – {WEEKDAYS_DE[day.weekday()]}, {date}" if tpl_title else f"{WEEKDAYS_DE[day.weekday()]}, {date}"


def run_recurring(today=None):
    """Fällige wiederkehrende Einträge anlegen. Verpasste Tage werden nicht nachgeholt:
    je Vorlage entsteht höchstens der zuletzt fällige Eintrag. Gibt die Zahl neuer Einträge zurück."""
    if not OWNS_LOCK or foreign_lock() is not None:
        return 0
    today = today or dt.date.today()
    made = 0
    with _write_lock:
        con = connect()
        cfg = meta_get(con, "recur", {}) or {}
        done = meta_get(con, "_recur_done", {}) or {}
        for tid, c in cfg.items():
            if not isinstance(c, dict):
                continue
            d = recur_due(c, today)
            if not d or done.get(tid, "") >= d.isoformat():
                continue
            t = con.execute("SELECT title FROM pages WHERE id=? AND kind='template' AND deleted=0", (tid,)).fetchone()
            if not t:
                continue
            create_from_template(con, tid, recur_title(t["title"], d), day=d, date_col=c.get("dateCol") or "")
            done[tid] = d.isoformat()
            made += 1
        if made:
            meta_set(con, "_recur_done", done)
            con.commit()
    return made


def _recur_loop():
    try:
        n = run_recurring()
        if n:
            print(f"{n} wiederkehrende Einträge angelegt.")
    except Exception as e:  # noqa: BLE001
        print("Wiederkehrende Einträge:", e)
    t = threading.Timer(15 * 60, _recur_loop)
    t.daemon = True
    t.start()


# ---------------------------------------------------------------------------
# Notion-Import
# ---------------------------------------------------------------------------

ID_RE = re.compile(r"^(.*?)\s*([0-9a-f]{32})$", re.I)
LINK_RE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)\s]+(?:\s[^)]*?)?)\)")
RELATION_RE = re.compile(r"([^,(]+?)\s*\(([^()]+?\.md)\)")
PROP_LINE_RE = re.compile(r"^([^:\n]{1,100}):\s?(.*)$")


def split_name(stem):
    """'Meine Seite 1a2b...' -> ('Meine Seite', '1a2b...') oder (stem, None)."""
    m = ID_RE.match(stem)
    if m and m.group(1):
        return m.group(1).strip(), m.group(2).lower()
    return stem.strip(), None


def norm_title(t):
    t = urllib.parse.unquote(t or "")
    return re.sub(r"[^0-9a-zäöüß]+", "", t.lower())


class NotionImporter:
    def __init__(self, con, log=print):
        self.con = con
        self.log = log
        self.root = None
        self.path_ids = {}      # normalisierter Pfad (ohne Endung) -> id
        self.link_titles = {}   # Datenbank-ID -> Originaltitel aus dem Linktext
        self.stats = {"pages": 0, "databases": 0, "rows": 0, "files": 0}

    # ----- Vorbereitung ----------------------------------------------------
    def run(self, source):
        tmp = None
        try:
            if os.path.isdir(source):
                root = source
            else:
                tmp = tempfile.mkdtemp(prefix="notion-import-")
                self.log(f"Entpacke {source} …")
                self._extract(source, tmp)
                root = tmp
            # Notion verpackt große Exporte als ZIPs im ZIP
            for _ in range(3):
                inner = [f for f in os.listdir(root) if f.lower().endswith(".zip")]
                if not inner:
                    break
                for f in inner:
                    p = os.path.join(root, f)
                    self.log(f"Entpacke inneres Archiv {f} …")
                    self._extract(p, root)
                    os.remove(p)
            # einzelnen Hüllordner überspringen
            while True:
                entries = [e for e in os.listdir(root) if not e.startswith(".") and e != "__MACOSX"]
                if len(entries) == 1 and os.path.isdir(os.path.join(root, entries[0])):
                    only = os.path.join(root, entries[0])
                    if not (os.path.exists(only + ".md") or os.path.exists(only + ".csv")):
                        root = only
                        continue
                break
            self.root = root
            with _write_lock:
                self._index(root)
                self._import_dir(root, None)
                # Dateinamen verlieren Sonderzeichen ("To Do`s" -> "To Do s"); der Linktext kennt das Original
                for pid, label in self.link_titles.items():
                    r = self.con.execute("SELECT title FROM pages WHERE id=? AND kind='database'", (pid,)).fetchone()
                    if r and r["title"] != label and norm_title(r["title"]) == norm_title(label):
                        self.con.execute("UPDATE pages SET title=? WHERE id=?", (label, pid))
                self.con.commit()
            self.log("Import fertig: {pages} Seiten, {databases} Datenbanken, "
                     "{rows} Einträge, {files} Dateien".format(**self.stats))
            return self.stats
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _extract(zpath, dest):
        with zipfile.ZipFile(zpath) as z:
            for info in z.infolist():
                name = info.filename
                # Notion/Windows-Exporte können Dateinamen in cp437 enthalten
                if not (info.flag_bits & 0x800):
                    try:
                        name = name.encode("cp437").decode("utf-8")
                    except (UnicodeEncodeError, UnicodeDecodeError):
                        pass
                name = name.replace("\\", "/")
                target = os.path.normpath(os.path.join(dest, name))
                if not target.startswith(os.path.abspath(dest)):
                    continue  # Schutz vor "../"-Pfaden
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)

    def _key(self, path):
        rel = os.path.relpath(path, self.root)
        stem, _ = os.path.splitext(rel)
        if stem.endswith("_all"):
            stem = stem[:-4]
        return os.path.normcase(os.path.normpath(stem))

    def _id_for(self, path):
        """ID für eine .md/.csv-Datei oder einen Ordner gleichen Namens."""
        key = self._key(path) if os.path.splitext(path)[1] else os.path.normcase(
            os.path.normpath(os.path.relpath(path, self.root)))
        if key in self.path_ids:
            return self.path_ids[key]
        _, nid = split_name(os.path.basename(key))
        if not nid:
            nid = hashlib.sha1(key.encode("utf-8")).hexdigest()[:32]
        self.path_ids[key] = nid
        return nid

    def _index(self, root):
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.lower().endswith((".md", ".csv")):
                    self._id_for(os.path.join(dirpath, f))

    # ----- Rekursiver Import ----------------------------------------------
    def _import_dir(self, folder, parent_id, db_ctx=None):
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            return
        files = [e for e in entries if os.path.isfile(os.path.join(folder, e))]
        dirs = [e for e in entries if os.path.isdir(os.path.join(folder, e)) and e != "__MACOSX"]
        stems = {os.path.splitext(f)[0] for f in files if f.lower().endswith((".md", ".csv"))}
        handled_rows = set()

        # Datenbanken (CSV) zuerst – bevorzugt die *_all.csv-Variante
        csvs = {}
        for f in files:
            if f.lower().endswith(".csv"):
                stem = f[:-4]
                base = stem[:-4] if stem.endswith("_all") else stem
                if base not in csvs or stem.endswith("_all"):
                    csvs[base] = f
        for base, f in csvs.items():
            handled_rows |= self._import_database(os.path.join(folder, f), folder, base, parent_id)

        # Normale Seiten
        for f in files:
            if not f.lower().endswith(".md"):
                continue
            if f in handled_rows or (db_ctx and f in db_ctx.get("handled", set())):
                continue
            path = os.path.join(folder, f)
            if db_ctx:
                self._import_row_page(path, db_ctx, None)
                continue
            pid = self._import_page(path, parent_id)
            sub = os.path.join(folder, f[:-3])
            if os.path.isdir(sub):
                self._import_dir(sub, pid)

        # Ordner ohne zugehörige Seite (z. B. Teamspaces) als eigene Seite anlegen
        for d in dirs:
            if d in stems or d in csvs:
                continue
            if db_ctx:
                continue  # Anhang-Ordner von Datenbankzeilen
            sub = os.path.join(folder, d)
            if not any(fn.lower().endswith((".md", ".csv")) for _, _, fs in os.walk(sub) for fn in fs):
                continue  # reiner Anhang-Ordner
            title, _ = split_name(d)
            pid = self._id_for(sub)
            self._upsert(pid, parent_id, "page", title, "", source=os.path.relpath(sub, self.root))
            self.stats["pages"] += 1
            self._import_dir(sub, pid)

    def _read(self, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _parse_md(self, path):
        text = self._read(path).replace("\r\n", "\n")
        lines = text.split("\n")
        title = None
        if lines and lines[0].startswith("# "):
            title = lines[0][2:].strip()
            lines = lines[1:]
            while lines and not lines[0].strip():
                lines = lines[1:]
        if not title:
            title, _ = split_name(os.path.splitext(os.path.basename(path))[0])
        return title, lines

    def _import_page(self, path, parent_id):
        pid = self._id_for(path)
        title, lines = self._parse_md(path)
        content = self._rewrite_links("\n".join(lines).strip() + "\n", path, pid)
        self._upsert(pid, parent_id, "page", title, content, source=os.path.relpath(path, self.root))
        self.stats["pages"] += 1
        return pid

    def _import_database(self, csv_path, folder, base, parent_id):
        db_id = self._id_for(csv_path)
        title, _ = split_name(base)
        text = self._read(csv_path)
        reader = list(csv.reader(io.StringIO(text)))
        header = reader[0] if reader else ["Name"]
        rows = reader[1:]
        header = [h.strip() or f"Spalte {i+1}" for i, h in enumerate(header)]
        title_col = header[0]

        row_folder = os.path.join(folder, base)
        md_files = {}
        if os.path.isdir(row_folder):
            for f in sorted(os.listdir(row_folder)):
                if f.lower().endswith(".md"):
                    t, _ = split_name(f[:-3])
                    md_files.setdefault(norm_title(t), []).append(f)

        # Spaltentypen erraten
        columns = []
        for i, name in enumerate(header):
            values = [r[i] if i < len(r) else "" for r in rows]
            ctype = "title" if i == 0 else guess_type(name, values)
            col = {"name": name, "type": ctype}
            if ctype in ("select", "multi_select"):
                col["options"] = ordered_options(values, ctype == "multi_select")
            columns.append(col)

        # Bei erneutem Import: lokal angepasste Typen, Reihenfolgen und Farben behalten
        old = self.con.execute("SELECT schema FROM pages WHERE id=?", (db_id,)).fetchone()
        if old:
            old_cols = {c["name"]: c for c in json.loads(old["schema"] or "[]")}
            for col in columns:
                o = old_cols.get(col["name"])
                if not o or col["type"] == "title":
                    continue
                col["type"] = o.get("type", col["type"])
                if col["type"] in ("select", "multi_select"):
                    opts = [dict(x) for x in o.get("options", [])]
                    names = {x["name"] for x in opts}
                    idx = header.index(col["name"])
                    vals = [r[idx] if idx < len(r) else "" for r in rows]
                    opts += [x for x in ordered_options(vals, col["type"] == "multi_select")
                             if x["name"] not in names]
                    col["options"] = opts

        # Datenbank-Seite (Beschreibung ggf. aus gleichnamiger .md)
        desc = ""
        db_md = os.path.join(folder, base + ".md")
        if os.path.isfile(db_md):
            _, lines = self._parse_md(db_md)
            desc = self._rewrite_links("\n".join(lines).strip(), db_md, db_id)
        self._upsert(db_id, parent_id, "database", title, desc,
                     schema=columns, source=os.path.relpath(csv_path, self.root))
        self.stats["databases"] += 1

        handled = set()
        ctx = {"db_id": db_id, "columns": columns, "folder": row_folder, "handled": handled}
        for n, r in enumerate(rows):
            if not any(c.strip() for c in r):
                continue
            props = {}
            for i, col in enumerate(columns):
                val = r[i] if i < len(r) else ""
                props[col["name"]] = self._rewrite_cell(val, col["type"], row_folder, csv_path)
            row_title = (r[0] if r else "").strip()
            md = None
            cands = md_files.get(norm_title(row_title))
            if cands:
                md = cands.pop(0)
            if md:
                handled.add(md)
                self._import_row_page(os.path.join(row_folder, md), ctx, props, sort=n)
            else:
                rid = hashlib.sha1(f"{db_id}:{n}:{row_title}".encode()).hexdigest()[:32]
                self._upsert(rid, db_id, "row", row_title, "", props=props, sort=n)
                self.stats["rows"] += 1

        # Restliche .md im Zeilenordner (nicht in CSV) ebenfalls importieren
        if os.path.isdir(row_folder):
            self._import_dir(row_folder, db_id, db_ctx=ctx)
        return {base + ".md"} if os.path.isfile(db_md) else set()

    def _import_row_page(self, path, ctx, props, sort=None):
        rid = self._id_for(path)
        title, lines = self._parse_md(path)
        colnames = {c["name"] for c in ctx["columns"]}
        parsed = {}
        # Notion schreibt Eigenschaften als "Name: Wert"-Zeilen an den Anfang
        i = 0
        while i < len(lines):
            m = PROP_LINE_RE.match(lines[i])
            if m and m.group(1).strip() in colnames:
                parsed[m.group(1).strip()] = m.group(2).strip()
                i += 1
                continue
            break
        if parsed:
            lines = lines[i:]
            while lines and not lines[0].strip():
                lines = lines[1:]
        if props is None:
            props = {c["name"]: "" for c in ctx["columns"]}
            for k, v in parsed.items():
                ctype = next((c["type"] for c in ctx["columns"] if c["name"] == k), "text")
                props[k] = self._rewrite_cell(v, ctype, os.path.dirname(path), path)
            props[ctx["columns"][0]["name"]] = title
        content = self._rewrite_links("\n".join(lines).strip() + "\n", path, rid)
        self._upsert(rid, ctx["db_id"], "row", title, content, props=props,
                     sort=sort if sort is not None else 10_000 + self.stats["rows"],
                     source=os.path.relpath(path, self.root))
        self.stats["rows"] += 1
        sub = path[:-3]
        if os.path.isdir(sub):
            # Unterseiten einer Datenbankzeile
            for f in sorted(os.listdir(sub)):
                fp = os.path.join(sub, f)
                if f.lower().endswith(".md"):
                    spid = self._import_page(fp, rid)
                    if os.path.isdir(fp[:-3]):
                        self._import_dir(fp[:-3], spid)
                elif f.lower().endswith(".csv"):
                    pass
            # Inline-Datenbanken in der Zeile
            for f in sorted(os.listdir(sub)):
                if f.lower().endswith(".csv"):
                    stem = f[:-4]
                    base = stem[:-4] if stem.endswith("_all") else stem
                    if stem.endswith("_all") or not os.path.exists(os.path.join(sub, stem + "_all.csv")):
                        self._import_database(os.path.join(sub, f), sub, base, rid)

    # ----- Links & Dateien --------------------------------------------------
    def _copy_asset(self, abs_path, owner_id):
        dest_dir = os.path.join(FILES_DIR, owner_id)
        os.makedirs(dest_dir, exist_ok=True)
        name = os.path.basename(abs_path)
        dest = os.path.join(dest_dir, name)
        if not os.path.exists(dest):
            shutil.copy2(abs_path, dest)
            self.stats["files"] += 1
        return "/dateien/" + urllib.parse.quote(owner_id) + "/" + urllib.parse.quote(name)

    def _resolve_target(self, target, base_file):
        if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
            return None
        decoded = urllib.parse.unquote(target.split("#")[0])
        p = os.path.normpath(os.path.join(os.path.dirname(base_file), decoded))
        return p

    def _link_for(self, target, base_file, owner_id):
        p = self._resolve_target(target, base_file)
        if p is None:
            # Notion-Weblinks auf Seiten -> lokale Links, falls ID bekannt
            m = re.search(r"notion\.so/.*?([0-9a-f]{32})", target)
            if m and m.group(1) in self.path_ids.values():
                return "#/p/" + m.group(1)
            return target
        ext = os.path.splitext(p)[1].lower()
        if ext in (".md", ".csv"):
            return "#/p/" + self._id_for(p)
        if os.path.isfile(p):
            return self._copy_asset(p, owner_id)
        return target

    def _rewrite_links(self, text, base_file, owner_id):
        def repl(m):
            bang, label, target = m.group(1), m.group(2), m.group(3)
            title = ""
            if " " in target:
                target, title = target.split(" ", 1)
                title = " " + title
            link = self._link_for(target, base_file, owner_id)
            if not bang and link.startswith("#/p/") and target.split("#")[0].lower().endswith(".csv"):
                self.link_titles[link[4:]] = label.replace("\\", "")
            return f"{bang}[{label}]({link}{title})"
        text = LINK_RE.sub(repl, text)
        # HTML-<img src="…"> (kommt in Notion-Exporten gelegentlich vor)
        def img(m):
            return m.group(1) + self._link_for(m.group(2), base_file, owner_id) + m.group(3)
        return re.sub(r'(<img[^>]*?src=")([^"]+)(")', img, text)

    def _rewrite_cell(self, value, ctype, folder, base_file):
        value = (value or "").strip()
        if not value:
            return ""
        if ctype == "date":
            return norm_date(value)
        if ctype == "checkbox":
            return "Yes" if value in ("Yes", "Ja") else "No"
        if ".md)" in value:
            def rel(m):
                p = self._resolve_target(m.group(2), base_file)
                if p is None:
                    return m.group(0)
                return f"[{m.group(1).strip()}](#/p/{self._id_for(p)})"
            return RELATION_RE.sub(rel, value)
        if ctype == "files":
            out = []
            for part in [x.strip() for x in value.split(",")]:
                link = self._link_for(urllib.parse.quote(urllib.parse.unquote(part)), base_file,
                                      self._id_for(base_file))
                name = os.path.basename(urllib.parse.unquote(part))
                out.append(f"[{name}]({link})")
            return ", ".join(out)
        return value

    def _upsert(self, pid, parent_id, kind, title, content, props=None, schema=None, sort=0, source=""):
        ts = now()
        self.con.execute(
            """INSERT INTO pages (id,parent_id,kind,title,icon,content,props,schema,sort,created,updated,deleted,source)
               VALUES (?,?,?,?,'',?,?,?,?,?,?,0,?)
               ON CONFLICT(id) DO UPDATE SET parent_id=excluded.parent_id, kind=excluded.kind,
                 title=excluded.title, content=excluded.content, props=excluded.props,
                 schema=excluded.schema, sort=excluded.sort, updated=excluded.updated,
                 deleted=0, source=excluded.source""",
            (pid, parent_id, kind, title, content, json.dumps(props or {}, ensure_ascii=False),
             json.dumps(schema or [], ensure_ascii=False), sort, ts, ts, source))


DATE_RE = re.compile(
    r"^(\d{4}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[./]\d{1,2}[./]\d{2,4}|"
    r"(jan|feb|mar|mär|apr|may|mai|jun|jul|aug|sep|oct|okt|nov|dec|dez)[a-zä]*\.? \d{1,2},? \d{4}|"
    r"\d{1,2}\.? (jan|feb|mär|mar|apr|mai|may|jun|jul|aug|sep|okt|oct|nov|dez|dec)[a-zä]*\.? \d{4})",
    re.I)


MONTHS = {"jan": 1, "feb": 2, "mar": 3, "mär": 3, "apr": 4, "may": 5, "mai": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12}


def _norm_one_date(v):
    v = re.sub(r"\s*\([^)]*\)\s*$", "", v.strip())
    y = mo = d = hh = mm = None
    m = re.match(r"^([A-Za-zä]+)\.? (\d{1,2}), (\d{4})(?:,? (\d{1,2}):(\d{2})\s*([AaPp][Mm])?)?$", v)
    if m and m.group(1)[:3].lower() in MONTHS:
        mo, d, y = MONTHS[m.group(1)[:3].lower()], int(m.group(2)), int(m.group(3))
        if m.group(4):
            hh, mm = int(m.group(4)), int(m.group(5))
            ap = (m.group(6) or "").lower()
            if ap == "pm" and hh < 12:
                hh += 12
            if ap == "am" and hh == 12:
                hh = 0
    if y is None:
        m = re.match(r"^(\d{1,2})\.? ([A-Za-zä]+)\.? (\d{4})(?:,? (\d{1,2}):(\d{2}))?$", v)
        if m and m.group(2)[:3].lower() in MONTHS:
            d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
            if m.group(4):
                hh, mm = int(m.group(4)), int(m.group(5))
    if y is None:
        m = re.match(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?", v)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if m.group(4):
                hh, mm = int(m.group(4)), int(m.group(5))
    if y is None:
        m = re.match(r"^(\d{1,2})([./])(\d{1,2})[./](\d{4})(?:,? (\d{1,2}):(\d{2}))?$", v)
        if m:
            a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
            if m.group(2) == "." or a > 12:
                d, mo = a, b
            elif b > 12:
                mo, d = a, b
            else:
                return None  # 03/04/2026 ist nicht eindeutig -> Originaltext behalten
            if m.group(5):
                hh, mm = int(m.group(5)), int(m.group(6))
    if y is None:
        return None
    try:
        dt.date(y, mo, d)
    except ValueError:
        return None
    out = f"{y:04d}-{mo:02d}-{d:02d}"
    if hh is not None:
        out += f" {hh:02d}:{mm:02d}"
    return out


def norm_date(value):
    """Notion-Datumsangaben ('November 10, 2026', '10. November 2026 → …') nach ISO wandeln."""
    if not value or not value.strip():
        return ""
    parts = [_norm_one_date(x) for x in re.split(r"\s*(?:→|->)\s*", value.strip())]
    if any(x is None for x in parts):
        return value.strip()
    return " → ".join(parts)


def ordered_options(values, multi):
    seen = []
    for v in values:
        for t in (v.split(",") if multi else [v]):
            t = t.strip()
            if t and t not in seen:
                seen.append(t)
    return [{"name": t} for t in seen]


def guess_type(name, values):
    vals = [v.strip() for v in values if v and v.strip()]
    lname = name.lower()
    if not vals:
        return "text"
    if all(v in ("Yes", "No", "Ja", "Nein") for v in vals):
        return "checkbox"
    if all(re.fullmatch(r"-?[\d.,]+\s?%?€?\$?", v) for v in vals):
        return "number"
    if all(DATE_RE.match(v) for v in vals):
        return "date"
    if all(re.match(r"^https?://\S+$", v) for v in vals):
        return "url"
    if all(re.match(r"^[^@\s]+@[^@\s]+\.\w+$", v) for v in vals):
        return "email"
    if any(".md)" in v for v in vals):
        return "relation"
    if lname in ("files", "dateien", "files & media", "dateien und medien", "anhang", "attachments"):
        return "files"
    if all(len(v) <= 60 for v in vals):
        tokens = [t.strip() for v in vals for t in v.split(",")]
        distinct = set(tokens)
        if any("," in v for v in vals) and len(distinct) <= max(12, len(tokens) // 2):
            return "multi_select"
        if len(distinct) <= max(8, len(vals) // 3) and len(vals) >= 2:
            return "select"
    return "text"


# ---------------------------------------------------------------------------
# Export (Markdown + CSV) – damit die Daten nie wieder eingesperrt sind
# ---------------------------------------------------------------------------

def safe_name(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "").strip().rstrip(".")
    return (s or "Ohne Titel")[:120]


def export_all(con, dest):
    os.makedirs(dest, exist_ok=True)
    rows = [row_to_page(r) for r in con.execute("SELECT * FROM pages WHERE deleted=0 AND kind!='template'")]
    by_parent = {}
    for p in rows:
        by_parent.setdefault(p["parent_id"], []).append(p)
    ids = {p["id"] for p in rows}
    roots = [p for p in rows if p["parent_id"] not in ids]
    titles = {p["id"]: p["title"] or "Ohne Titel" for p in rows}
    schemas = {p["id"]: p["schema"] for p in rows if p["kind"] == "database"}

    def rel_ids(v):
        return [x.strip() for x in str(v or "").split(",") if x.strip()]

    def cell(row, col):
        """Wert einer Spalte für den Export; Verknüpfungen werden zu Titeln."""
        if col["type"] == "title":
            return row["title"]
        if col["type"] == "relation" and col.get("db"):
            if col.get("mirror"):
                hits = [c for c in by_parent.get(col["db"], []) if c["kind"] == "row"
                        and row["id"] in rel_ids(c["props"].get(col["mirror"]))]
                return ", ".join(titles[c["id"]] for c in hits)
            return ", ".join(titles.get(i, i) for i in rel_ids(row["props"].get(col["name"])))
        return row["props"].get(col["name"], "")

    def stored(schema):   # Rollups und Formeln werden nur in der App berechnet
        return [c for c in schema if c.get("type") not in ("rollup", "formula")]

    def fix_links(md, here_depth):
        md = re.sub(r"\(/dateien/", "(" + "../" * here_depth + "_dateien/", md)
        return md

    def write(p, folder, depth):
        name = safe_name(p["title"])
        base = os.path.join(folder, name)
        n = 2
        while os.path.exists(base + ".md") or os.path.exists(base + ".csv"):
            base = os.path.join(folder, f"{name} ({n})")
            n += 1
        children = sorted(by_parent.get(p["id"], []), key=lambda c: (c["sort"], c["title"]))
        if p["kind"] == "database":
            cols = stored(p["schema"])
            with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow([c["name"] for c in cols])
                for c in children:
                    if c["kind"] == "row":
                        w.writerow([cell(c, col) for col in cols])
        head = f"# {p['title']}\n\n"
        if p["kind"] == "row":
            schema = schemas.get(p["parent_id"])
            if schema:
                vals = [(c["name"], cell(p, c)) for c in stored(schema) if c["type"] != "title"]
                known = {c["name"] for c in schema}
                vals += [(k, v) for k, v in p["props"].items() if k not in known]
            else:
                vals = list(p["props"].items())
            head += "".join(f"{k}: {v}\n" for k, v in vals if v) + "\n"
        if p["kind"] != "database" or p["content"].strip():
            with open(base + ".md", "w", encoding="utf-8") as fh:
                fh.write(head + fix_links(p["content"], depth))
        if children:
            os.makedirs(base, exist_ok=True)
            for c in children:
                write(c, base, depth + 1)

    for r in sorted(roots, key=lambda c: (c["sort"], c["title"])):
        write(r, dest, 0)
    if os.path.isdir(FILES_DIR):
        shutil.copytree(FILES_DIR, os.path.join(dest, "_dateien"), dirs_exist_ok=True)
    return len(rows)


def export_zip_bytes(con):
    tmp = tempfile.mkdtemp(prefix="notion-lokal-export-")
    try:
        export_all(con, os.path.join(tmp, "export"))
        out = os.path.join(tmp, "export.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _, files in os.walk(os.path.join(tmp, "export")):
                for f in files:
                    full = os.path.join(dirpath, f)
                    z.write(full, os.path.relpath(full, os.path.join(tmp, "export")))
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def backup():
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M")
    target = os.path.join(BASE_DIR, f"sicherung_{stamp}.zip")
    con = connect()
    snap = os.path.join(tempfile.mkdtemp(), "notion.sqlite3")
    dst = sqlite3.connect(snap)
    con.backup(dst)  # konsistente Kopie auch bei laufendem Server
    dst.close()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(snap, "daten/notion.sqlite3")
        for dirpath, _, files in os.walk(FILES_DIR):
            for f in files:
                full = os.path.join(dirpath, f)
                z.write(full, os.path.join("daten", os.path.relpath(full, DATA_DIR)))
    shutil.rmtree(os.path.dirname(snap), ignore_errors=True)
    return target


def auto_backup(keep=14, min_age_hours=20):
    """Legt höchstens einmal täglich eine Kopie der Datenbank in daten/sicherungen ab."""
    try:
        if not os.path.exists(DB_PATH):
            return None
        bdir = os.path.join(DATA_DIR, "sicherungen")
        os.makedirs(bdir, exist_ok=True)
        files = sorted(f for f in os.listdir(bdir) if f.endswith(".sqlite3"))
        if files:
            newest = os.path.getmtime(os.path.join(bdir, files[-1]))
            if (dt.datetime.now().timestamp() - newest) < min_age_hours * 3600:
                return None
        target = os.path.join(bdir, "notion_" + dt.datetime.now().strftime("%Y-%m-%d_%H%M") + ".sqlite3")
        src = connect()
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
        src.close()
        files.append(os.path.basename(target))
        for f in files[:-keep]:
            os.remove(os.path.join(bdir, f))
        return target
    except Exception as e:  # noqa: BLE001
        print("Automatische Sicherung fehlgeschlagen:", e)
        return None


def _backup_loop():
    auto_backup()
    t = threading.Timer(6 * 3600, _backup_loop)
    t.daemon = True
    t.start()


# ---------------------------------------------------------------------------
# Updates direkt aus dem GitHub-Repository
# ---------------------------------------------------------------------------

UPDATE_SOURCES = [u for u in os.environ.get("NOTION_LOKAL_UPDATE_URL", "").split(",") if u] or [
    "https://raw.githubusercontent.com/mattes-flowspace/der-redewert/main/notion-lokal/",
    "https://raw.githubusercontent.com/mattes-flowspace/der-redewert/claude/notion-offline-backup-p71d1a/notion-lokal/",
]
HTTPD = None
RESTART = False


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0")))


UPDATE_KEEP = 5      # so viele frühere Programmversionen bleiben in alte-versionen/ liegen


def local_info():
    try:
        with open(os.path.join(BASE_DIR, "version.json"), encoding="utf-8") as fh:
            info = json.load(fh)
            return info if isinstance(info, dict) else {}
    except (OSError, ValueError):
        return {}


def local_version():
    return local_info().get("version", "0")


def _norm_sha(data):
    """Prüfsumme unabhängig von Windows-/Unix-Zeilenenden."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def local_changes():
    """Programmdateien, die von der installierten Version abweichen, z. B. weil sie von Hand bearbeitet wurden."""
    out = []
    for rel, want in (local_info().get("sha256") or {}).items():
        try:
            with open(os.path.join(BASE_DIR, rel), "rb") as fh:
                got = _norm_sha(fh.read())
        except OSError:
            continue        # Datei fehlt: da geht beim Update nichts verloren
        if got != want:
            out.append(rel)
    return sorted(out)


def write_checksums():
    """Für Entwickler: Prüfsummen der Programmdateien in version.json eintragen (vor dem Veröffentlichen)."""
    info = local_info()
    sums = {}
    for rel in info.get("files", []):
        if rel == "version.json":
            continue
        with open(os.path.join(BASE_DIR, rel), "rb") as fh:
            sums[rel] = _norm_sha(fh.read())
    info["sha256"] = sums
    with open(os.path.join(BASE_DIR, "version.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(info, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return sums


def _fetch(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "notion-lokal-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_update():
    """Neueste Version aus den Quellen ermitteln. Gibt (info, quelle) oder (None, None) zurück."""
    best, src = None, None
    for base in UPDATE_SOURCES:
        try:
            info = json.loads(_fetch(base + "version.json").decode("utf-8"))
        except Exception:  # noqa: BLE001  (offline, Zweig existiert nicht …)
            continue
        if best is None or _vtuple(info.get("version")) > _vtuple(best.get("version")):
            best, src = info, base
    return best, src


def apply_update(force=False):
    info, base = check_update()
    if not info or _vtuple(info.get("version")) <= _vtuple(local_version()):
        return {"updated": False, "version": local_version()}
    changed = local_changes()
    if changed and not force:
        # Lokal bearbeitete Programmdateien nicht stillschweigend überschreiben
        return {"updated": False, "blocked": True, "modified": changed, "version": local_version()}
    sums = info.get("sha256") or {}
    files = {}
    for rel in info.get("files", []):
        target = os.path.normpath(os.path.join(BASE_DIR, rel))
        inside = target.startswith(BASE_DIR + os.sep)
        if not inside or os.path.isabs(rel) or target.startswith(os.path.abspath(DATA_DIR) + os.sep):
            raise ValueError(f"Unzulässiger Dateipfad im Update: {rel}")
        data = _fetch(base + urllib.parse.quote(rel), timeout=30)
        if not data.strip():
            raise ValueError(f"Leere Datei im Update: {rel}")
        if rel in sums and _norm_sha(data) != sums[rel]:
            raise ValueError(f"Datei im Update ist unvollständig oder verändert: {rel}")
        files[target] = data
    main_py = os.path.join(BASE_DIR, "app.py")
    if main_py in files:
        compile(files[main_py].decode("utf-8"), "app.py", "exec")   # kaputtes Update gar nicht erst einspielen
    # alte Programmdateien aufheben, dann ersetzen
    all_old = os.path.join(BASE_DIR, "alte-versionen")
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    old_dir = os.path.join(all_old, f"{safe_name(str(local_version()))}_{stamp}")
    for target, data in files.items():
        if os.path.exists(target):
            keep = os.path.join(old_dir, os.path.relpath(target, BASE_DIR))
            os.makedirs(os.path.dirname(keep), exist_ok=True)
            shutil.copy2(target, keep)
    try:    # nur die letzten Versionen behalten
        kept = sorted((d for d in os.listdir(all_old) if os.path.isdir(os.path.join(all_old, d))),
                      key=lambda d: os.path.getmtime(os.path.join(all_old, d)))
        for d in kept[:-UPDATE_KEEP]:
            shutil.rmtree(os.path.join(all_old, d), ignore_errors=True)
    except OSError:
        pass
    for target, data in files.items():
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".neu"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
    return {"updated": True, "version": info.get("version"), "restart": main_py in files,
            "backup": os.path.relpath(old_dir, BASE_DIR), "overwritten": changed}


def _restart_soon():
    global RESTART
    RESTART = True
    if HTTPD:
        threading.Thread(target=HTTPD.shutdown, daemon=True).start()


# ---------------------------------------------------------------------------
# Passwortschutz der Oberfläche
#
# Schützt den Zugriff über den Browser (z. B. im WLAN oder an einem gemeinsam
# genutzten Rechner). Die Datei daten/notion.sqlite3 selbst wird NICHT
# verschlüsselt: Wer den Datenordner lesen kann, kommt auch ohne Passwort an
# die Inhalte.
# ---------------------------------------------------------------------------

AUTH_KEY = "_zugang"          # meta-Schlüssel; alles mit "_" am Anfang bleibt für die Oberfläche unsichtbar
AUTH_COOKIE = "nl_sitzung"
AUTH_DAYS = 30
AUTH_ITER = 300_000
_auth_fails = []              # Zeitpunkte fehlgeschlagener Anmeldungen (Bremse gegen Durchprobieren)


def auth_load(con=None):
    con = con or connect()
    r = con.execute("SELECT value FROM meta WHERE key=?", (AUTH_KEY,)).fetchone()
    try:
        a = json.loads(r["value"]) if r else None
    except ValueError:
        a = None
    return a if a and a.get("hash") else None


def _pw_hash(pw, salt, it):
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it)).hex()


def auth_set(pw):
    """Passwort setzen oder (leer) entfernen. Ein neues Geheimnis meldet alle bisherigen Sitzungen ab."""
    with _write_lock:
        con = connect()
        if not pw:
            con.execute("DELETE FROM meta WHERE key=?", (AUTH_KEY,))
            con.commit()
            return None
        salt = secrets.token_hex(16)
        a = {"salt": salt, "iter": AUTH_ITER, "hash": _pw_hash(pw, salt, AUTH_ITER), "secret": secrets.token_hex(32)}
        con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (AUTH_KEY, json.dumps(a)))
        con.commit()
        return a


def auth_check(a, pw):
    return hmac.compare_digest(_pw_hash(pw or "", a["salt"], a.get("iter", AUTH_ITER)), a["hash"])


def session_make(a):
    exp = str(int(time.time()) + AUTH_DAYS * 86400)
    sig = hmac.new(bytes.fromhex(a["secret"]), exp.encode(), "sha256").hexdigest()
    return f"{exp}.{sig}"


def session_ok(a, tok):
    exp, _, sig = (tok or "").partition(".")
    if not exp.isdigit() or int(exp) < time.time():
        return False
    good = hmac.new(bytes.fromhex(a["secret"]), exp.encode(), "sha256").hexdigest()
    return hmac.compare_digest(good, sig)


# ---------------------------------------------------------------------------
# HTTP-Server + JSON-API
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "NotionLokal/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- Hilfsfunktionen ---
    def _json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _err(self, status, msg):
        self._json({"error": msg}, status)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _jbody(self):
        raw = self._body()
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _file(self, path, download_name=None):
        if not os.path.isfile(path):
            return self._err(404, "Nicht gefunden")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        if download_name:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as fh:
            shutil.copyfileobj(fh, self.wfile)

    # --- Passwortschutz ---
    def _session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == AUTH_COOKIE:
                return v
        return ""

    def _authed(self, a=None):
        a = a if a is not None else auth_load()
        return a is None or session_ok(a, self._session())

    def _gate(self, path):
        """True, wenn die Anfrage durch darf. Geschützt sind alle Daten (/api, /dateien), nicht die leere Oberfläche."""
        if not (path.startswith("/api/") or path.startswith("/dateien/")) or path.startswith("/api/auth"):
            return True
        if self._authed():
            return True
        self._err(401, "Bitte anmelden")
        return False

    def _cookie(self, tok):
        age = AUTH_DAYS * 86400 if tok else 0
        return {"Set-Cookie": f"{AUTH_COOKIE}={tok}; Path=/; Max-Age={age}; HttpOnly; SameSite=Strict"}

    def api_auth(self, sub, b):
        a = auth_load()
        if sub == "login":
            if a is None:
                return self._json({"enabled": False, "ok": True})
            now_ts = time.time()
            _auth_fails[:] = [t for t in _auth_fails if now_ts - t < 300]
            if len(_auth_fails) >= 8:
                return self._err(429, "Zu viele Fehlversuche. Bitte ein paar Minuten warten.")
            if not auth_check(a, b.get("password")):
                _auth_fails.append(now_ts)
                time.sleep(0.6)
                return self._err(403, "Falsches Passwort")
            _auth_fails.clear()
            return self._json({"enabled": True, "ok": True}, headers=self._cookie(session_make(a)))
        if sub == "logout":
            return self._json({"enabled": a is not None, "ok": a is None}, headers=self._cookie(""))
        if sub == "set":
            if not self._authed(a):
                return self._err(401, "Bitte anmelden")
            if a is not None and not auth_check(a, b.get("current")):
                time.sleep(0.6)
                return self._err(403, "Das aktuelle Passwort stimmt nicht")
            new = b.get("new") or ""
            if new and len(new) < 6:
                return self._err(400, "Das Passwort braucht mindestens 6 Zeichen")
            a2 = auth_set(new)
            return self._json({"enabled": a2 is not None, "ok": True}, headers=self._cookie(session_make(a2) if a2 else ""))
        return self._err(404, "Unbekannter Pfad")

    def _check_host(self):
        # Schutz vor DNS-Rebinding: lokal nur unter localhost/127.0.0.1 antworten
        if HOST not in ("127.0.0.1", "localhost", "::1"):
            return True
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        self._err(403, "Unbekannter Host")
        return False

    def _check_origin(self):
        # Nur Anfragen von der eigenen Oberfläche zulassen (Schutz vor fremden Webseiten)
        if not self._check_host():
            return False
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self._err(403, "Fremder Ursprung")
            return False
        # Schreiben nur, solange dieser Rechner die Sperre hält
        if not self.path.startswith(("/api/lock", "/api/auth/login", "/api/auth/logout")):
            other = foreign_lock()
            if other is not None or not OWNS_LOCK:
                if other is None:
                    take_lock()      # Sperrdatei fehlt (z. B. gelöscht) -> neu anlegen
                else:
                    self._json({"error": f"Notion Lokal ist auf „{other['host']}“ geöffnet", "lock": lock_status()}, 423)
                    return False
        return True

    # --- Routing ---
    def do_GET(self):
        if not self._check_host():
            return
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        q = urllib.parse.parse_qs(url.query)
        if not self._gate(path):
            return
        try:
            if path == "/api/auth":
                a = auth_load()
                return self._json({"enabled": a is not None, "ok": self._authed(a)})
            if path in ("/", "/index.html"):
                return self._file(os.path.join(STATIC_DIR, "index.html"))
            if path.startswith("/static/"):
                return self._safe_file(STATIC_DIR, path[len("/static/"):])
            if path.startswith("/dateien/"):
                return self._safe_file(FILES_DIR, path[len("/dateien/"):])
            if path == "/api/tree":
                return self.api_tree()
            if path.startswith("/api/page/"):
                return self.api_get_page(path.split("/")[3])
            if path.startswith("/api/history/"):
                parts = path.split("/")
                return self.api_history(parts[3], parts[4] if len(parts) > 4 and parts[4] else None)
            if path == "/api/search":
                return self.api_search((q.get("q") or [""])[0])
            if path == "/api/trash":
                return self.api_trash()
            if path == "/api/agenda":
                return self.api_agenda()
            if path == "/api/lock":
                return self._json(lock_status())
            if path == "/api/update":
                info, _src = check_update()
                cur = local_version()
                newer = bool(info) and _vtuple(info.get("version")) > _vtuple(cur)
                return self._json({"current": cur, "reachable": info is not None, "available": newer,
                                   "latest": (info or {}).get("version"), "notes": (info or {}).get("notes", ""),
                                   "modified": local_changes()})
            if path == "/api/meta":
                con = connect()
                return self._json({r["key"]: json.loads(r["value"]) for r in con.execute("SELECT * FROM meta")
                                   if not r["key"].startswith("_")})
            if path == "/api/export.zip":
                data = export_zip_bytes(connect())
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="notion-lokal-export-{dt.date.today()}.zip"')
                self.end_headers()
                self.wfile.write(data)
                return
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_POST(self):
        if not self._check_origin():
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if not self._gate(url.path):
            return
        try:
            if url.path.startswith("/api/auth/"):
                return self.api_auth(url.path.split("/")[3], self._jbody())
            if url.path == "/api/page":
                return self.api_create(self._jbody())
            if url.path.startswith("/api/restore/"):
                return self.api_restore(url.path.split("/")[3])
            if url.path.startswith("/api/history/"):
                parts = url.path.split("/")
                return self.api_history_restore(parts[3], parts[4] if len(parts) > 4 else "")
            if url.path == "/api/upload":
                return self.api_upload((q.get("page") or ["allgemein"])[0], (q.get("name") or ["datei"])[0])
            if url.path == "/api/import":
                return self.api_import()
            if url.path == "/api/update":
                res = apply_update(force=bool(self._jbody().get("force")))
                self._json(res)
                if res.get("restart"):
                    _restart_soon()
                return
            if url.path == "/api/lock/takeover":
                take_lock()
                return self._json(lock_status())
            if url.path == "/api/batch":
                return self.api_batch(self._jbody())
            if url.path == "/api/recur/run":
                return self._json({"made": run_recurring()})
            if url.path.startswith("/api/from-template/"):
                return self.api_from_template(url.path.split("/")[3], self._jbody())
            if url.path.startswith("/api/duplicate/"):
                return self.api_duplicate(url.path.split("/")[3])
            if url.path.startswith("/api/db/"):
                return self.api_db_op(url.path.split("/")[3], self._jbody())
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_PUT(self):
        if not self._check_origin():
            return
        url = urllib.parse.urlparse(self.path)
        if not self._gate(url.path):
            return
        try:
            if url.path.startswith("/api/page/"):
                return self.api_update(url.path.split("/")[3], self._jbody())
            if url.path.startswith("/api/meta/"):
                key = urllib.parse.unquote(url.path.split("/")[3])
                if key.startswith("_"):
                    return self._err(403, "Geschützter Schlüssel")
                with _write_lock:
                    con = connect()
                    con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                (key, json.dumps(self._jbody(), ensure_ascii=False)))
                    con.commit()
                return self._json({"ok": True})
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_DELETE(self):
        if not self._check_origin():
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        if not self._gate(url.path):
            return
        try:
            if url.path.startswith("/api/page/"):
                return self.api_delete(url.path.split("/")[3], (q.get("endgueltig") or ["0"])[0] == "1")
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def _safe_file(self, base, rel):
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(os.path.abspath(base) + os.sep):
            return self._err(403, "Verboten")
        return self._file(full)

    # --- API ---
    def api_tree(self):
        con = connect()
        rows = con.execute(
            "SELECT id,parent_id,kind,title,icon,sort FROM pages WHERE deleted=0 AND kind NOT IN ('row','template') "
            "ORDER BY sort, title COLLATE NOCASE").fetchall()
        self._json([dict(r) for r in rows])

    def api_get_page(self, pid):
        con = connect()
        r = con.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone()
        if not r:
            return self._err(404, "Seite nicht gefunden")
        page = row_to_page(r)
        # Brotkrumen
        crumbs, cur, seen = [], r["parent_id"], set()
        while cur and cur not in seen:
            seen.add(cur)
            pr = con.execute("SELECT id,parent_id,title,icon,kind FROM pages WHERE id=?", (cur,)).fetchone()
            if not pr:
                break
            crumbs.insert(0, {"id": pr["id"], "title": pr["title"], "icon": pr["icon"], "kind": pr["kind"]})
            cur = pr["parent_id"]
        page["breadcrumbs"] = crumbs
        if page["kind"] == "database":
            page["rows"] = [row_to_page(x) for x in con.execute(
                "SELECT * FROM pages WHERE parent_id=? AND kind='row' AND deleted=0 ORDER BY sort, title",
                (pid,))]
            for x in page["rows"]:
                c = x.pop("content", "") or ""
                x["has_content"] = bool(c.strip())
                # erstes Bild im Inhalt als Vorschau für die Galerie
                m = re.search(r"!\[[^\]]*\]\(([^)\s]+)", c)
                x["thumb"] = m.group(1) if m else ""
            page["templates"] = [dict(x) for x in con.execute(
                "SELECT id,title,icon FROM pages WHERE parent_id=? AND kind='template' AND deleted=0 "
                "ORDER BY sort, title COLLATE NOCASE", (pid,))]
        if page["kind"] in ("row", "template"):
            db = con.execute("SELECT schema,title FROM pages WHERE id=?", (r["parent_id"],)).fetchone()
            page["db_schema"] = json.loads(db["schema"]) if db else []
            page["db_title"] = db["title"] if db else ""
        page["children"] = [dict(x) for x in con.execute(
            "SELECT id,title,icon,kind FROM pages WHERE parent_id=? AND kind NOT IN ('row','template') AND deleted=0 "
            "ORDER BY sort, title COLLATE NOCASE", (pid,))]
        self._json(page)

    def api_create(self, b):
        kind = b.get("kind", "page")
        if kind not in ("page", "database", "row", "template"):
            return self._err(400, "Ungültiger Typ")
        pid, ts = new_id(), now()
        schema = b.get("schema") or ([{"name": "Name", "type": "title"},
                                      {"name": "Status", "type": "select", "options": [
                                          {"name": "Offen", "color": "gray"}, {"name": "In Arbeit", "color": "blue"},
                                          {"name": "Erledigt", "color": "green"}]},
                                      {"name": "Datum", "type": "date"}] if kind == "database" else [])
        props = b.get("props") or {}
        title = b.get("title", "")
        with _write_lock:
            con = connect()
            mx = con.execute("SELECT COALESCE(MAX(sort),0)+1 FROM pages WHERE parent_id IS ?",
                             (b.get("parent_id"),)).fetchone()[0]
            con.execute(
                "INSERT INTO pages (id,parent_id,kind,title,icon,content,props,schema,sort,created,updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, b.get("parent_id"), kind, title, b.get("icon", ""), b.get("content", ""),
                 json.dumps(props, ensure_ascii=False), json.dumps(schema, ensure_ascii=False), mx, ts, ts))
            con.commit()
        self._json({"id": pid})

    def _apply_update(self, con, pid, b):
        fields, vals = [], []
        for k in ("title", "icon", "cover", "content", "parent_id", "sort"):
            if k in b:
                fields.append(f"{k}=?")
                vals.append(b[k])
        if "props" in b:
            fields.append("props=?")
            vals.append(json.dumps(b["props"], ensure_ascii=False))
        if "schema" in b:
            fields.append("schema=?")
            vals.append(json.dumps(b["schema"], ensure_ascii=False))
        if not fields:
            return None
        snapshot(con, pid, {k: b[k] for k in ("title", "icon", "content", "props") if k in b})
        fields.append("updated=?")
        vals.append(now())
        if b.get("parent_id"):
            # Verhindern, dass eine Seite in sich selbst verschoben wird
            cur = b["parent_id"]
            while cur:
                if cur == pid:
                    return "Eine Seite kann nicht in sich selbst verschoben werden"
                row = con.execute("SELECT parent_id FROM pages WHERE id=?", (cur,)).fetchone()
                cur = row["parent_id"] if row else None
        con.execute(f"UPDATE pages SET {', '.join(fields)} WHERE id=?", (*vals, pid))
        # Titel einer Datenbankzeile mit Titelspalte synchron halten
        r = con.execute("SELECT kind,parent_id,title,props FROM pages WHERE id=?", (pid,)).fetchone()
        if r and r["kind"] in ("row", "template"):
            db = con.execute("SELECT schema FROM pages WHERE id=?", (r["parent_id"],)).fetchone()
            schema = json.loads(db["schema"]) if db else []
            tcol = next((c["name"] for c in schema if c["type"] == "title"), None)
            if tcol:
                props = json.loads(r["props"])
                if "title" in b:
                    props[tcol] = b["title"]
                    con.execute("UPDATE pages SET props=? WHERE id=?",
                                (json.dumps(props, ensure_ascii=False), pid))
                elif "props" in b and tcol in props:
                    con.execute("UPDATE pages SET title=? WHERE id=?", (props[tcol], pid))
        return None

    def api_update(self, pid, b):
        with _write_lock:
            con = connect()
            err = self._apply_update(con, pid, b)
            if err:
                return self._err(400, err)
            con.commit()
        self._json({"ok": True})

    def api_batch(self, b):
        with _write_lock:
            con = connect()
            for u in b.get("updates", []):
                err = self._apply_update(con, u["id"], u)
                if err:
                    con.rollback()
                    return self._err(400, err)
            con.commit()
        self._json({"ok": True})

    def api_duplicate(self, pid):
        with _write_lock:
            con = connect()
            r = con.execute("SELECT * FROM pages WHERE id=?", (pid,)).fetchone()
            if not r:
                return self._err(404, "Seite nicht gefunden")
            nid, ts = new_id(), now()
            title = (r["title"] or "Ohne Titel") + " (Kopie)"
            props = json.loads(r["props"] or "{}")
            if r["kind"] in ("row", "template"):
                db = con.execute("SELECT schema FROM pages WHERE id=?", (r["parent_id"],)).fetchone()
                tcol = next((c["name"] for c in json.loads(db["schema"]) if c["type"] == "title"), None) if db else None
                if tcol:
                    props[tcol] = title
            con.execute(
                "INSERT INTO pages (id,parent_id,kind,title,icon,cover,content,props,schema,sort,created,updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (nid, r["parent_id"], r["kind"], title, r["icon"], r["cover"], r["content"],
                 json.dumps(props, ensure_ascii=False), r["schema"], r["sort"] + 0.5, ts, ts))
            con.commit()
        self._json({"id": nid})

    def api_from_template(self, tid, b):
        """Neuen Eintrag aus einer Vorlage anlegen: Icon, Cover, Eigenschaften, Inhalt und Dateien übernehmen."""
        with _write_lock:
            con = connect()
            row = create_from_template(con, tid, (b.get("title") or "").strip(), b.get("props") or {})
            if not row:
                return self._err(404, "Vorlage nicht gefunden")
            con.commit()
        self._json(row)

    def api_db_op(self, db_id, b):
        """Spalten/Optionen umbenennen oder löschen – inklusive aller Einträge, in einer Transaktion."""
        op = b.get("op")
        with _write_lock:
            con = connect()
            db = con.execute("SELECT schema FROM pages WHERE id=? AND kind='database'", (db_id,)).fetchone()
            if not db:
                return self._err(404, "Datenbank nicht gefunden")
            schema = json.loads(db["schema"])
            rows = con.execute("SELECT id,props FROM pages WHERE parent_id=? AND kind IN ('row','template')", (db_id,)).fetchall()

            def save_rows(fn):
                for r in rows:
                    props = json.loads(r["props"] or "{}")
                    if fn(props):
                        con.execute("UPDATE pages SET props=? WHERE id=?",
                                    (json.dumps(props, ensure_ascii=False), r["id"]))

            if op == "rename_column":
                old, new = b["from"], b["to"].strip()
                if not new or any(c["name"] == new for c in schema):
                    return self._err(400, "Name ist leer oder existiert schon")
                for c in schema:
                    if c["name"] == old:
                        c["name"] = new

                def fn(props):
                    if old in props:
                        props[new] = props.pop(old)
                        return True
                save_rows(fn)
            elif op in ("rename_option", "delete_option"):
                col = next((c for c in schema if c["name"] == b["column"]), None)
                if not col:
                    return self._err(404, "Spalte nicht gefunden")
                old = b.get("from") or b.get("name")
                new = (b.get("to") or "").strip() if op == "rename_option" else None
                if op == "rename_option" and (not new or "," in new):
                    return self._err(400, "Ungültiger Name")
                opts = col.get("options", [])
                if op == "rename_option":
                    if any(o["name"] == new for o in opts if o["name"] != old):
                        opts = [o for o in opts if o["name"] != old]  # zusammenführen
                    for o in opts:
                        if o["name"] == old:
                            o["name"] = new
                else:
                    opts = [o for o in opts if o["name"] != old]
                col["options"] = opts
                multi = col["type"] == "multi_select"

                def fn(props):
                    v = props.get(col["name"], "")
                    if not v:
                        return False
                    toks = [t.strip() for t in v.split(",")] if multi else [v.strip()]
                    if old not in toks:
                        return False
                    out = []
                    for t in toks:
                        t = new if t == old else t
                        if t and t not in out:
                            out.append(t)
                    props[col["name"]] = ", ".join(out)
                    return True
                save_rows(fn)
            else:
                return self._err(400, "Unbekannte Operation")
            con.execute("UPDATE pages SET schema=?, updated=? WHERE id=?",
                        (json.dumps(schema, ensure_ascii=False), now(), db_id))
            con.commit()
        self._json({"ok": True, "schema": schema})

    def _descendants(self, con, pid):
        out, stack = [pid], [pid]
        while stack:
            cur = stack.pop()
            for r in con.execute("SELECT id FROM pages WHERE parent_id=?", (cur,)):
                out.append(r["id"])
                stack.append(r["id"])
        return out

    def api_delete(self, pid, hard):
        with _write_lock:
            con = connect()
            ids = self._descendants(con, pid)
            marks = ",".join("?" * len(ids))
            if hard:
                con.execute(f"DELETE FROM pages WHERE id IN ({marks})", ids)
                con.execute(f"DELETE FROM versions WHERE page_id IN ({marks})", ids)
                for i in ids:
                    shutil.rmtree(os.path.join(FILES_DIR, i), ignore_errors=True)
            else:
                con.execute(f"UPDATE pages SET deleted=1, updated=? WHERE id IN ({marks})", (now(), *ids))
                # Nur die oberste Seite merkt sich, dass sie direkt gelöscht wurde
                con.execute("UPDATE pages SET deleted=2 WHERE id=?", (pid,))
            con.commit()
        self._json({"ok": True, "count": len(ids)})

    def api_restore(self, pid):
        with _write_lock:
            con = connect()
            ids = self._descendants(con, pid)
            marks = ",".join("?" * len(ids))
            con.execute(f"UPDATE pages SET deleted=0 WHERE id IN ({marks})", ids)
            # Elternseite im Papierkorb? Dann an die oberste Ebene hängen
            r = con.execute("SELECT parent_id FROM pages WHERE id=?", (pid,)).fetchone()
            if r and r["parent_id"]:
                par = con.execute("SELECT deleted FROM pages WHERE id=?", (r["parent_id"],)).fetchone()
                if not par or par["deleted"]:
                    con.execute("UPDATE pages SET parent_id=NULL WHERE id=?", (pid,))
            con.commit()
        self._json({"ok": True})

    def api_history(self, pid, vid):
        con = connect()
        if vid:
            r = con.execute("SELECT * FROM versions WHERE page_id=? AND id=?", (pid, vid)).fetchone()
            if not r:
                return self._err(404, "Fassung nicht gefunden")
            d = dict(r)
            d["props"] = json.loads(d["props"] or "{}")
            return self._json(d)
        rows = con.execute("SELECT id,ts,title,icon,length(content) AS chars,reason FROM versions "
                           "WHERE page_id=? ORDER BY id DESC", (pid,)).fetchall()
        self._json([dict(r) for r in rows])

    def api_history_restore(self, pid, vid):
        with _write_lock:
            con = connect()
            v = con.execute("SELECT * FROM versions WHERE page_id=? AND id=?", (pid, vid)).fetchone()
            if not v:
                return self._err(404, "Fassung nicht gefunden")
            # den jetzigen Stand sichern, damit auch das Wiederherstellen rückgängig gemacht werden kann
            snapshot(con, pid, reason="vor dem Wiederherstellen", force=True)
            b = {"title": v["title"], "icon": v["icon"], "content": v["content"],
                 "props": json.loads(v["props"] or "{}")}
            err = self._apply_update(con, pid, b)
            if err:
                con.rollback()
                return self._err(400, err)
            con.commit()
        self._json({"ok": True})

    def api_agenda(self):
        """Alle Datenbanken mit Datumsspalte samt Einträgen (ohne Inhalt) für die Heute-Übersicht."""
        try:
            run_recurring()
        except Exception:  # noqa: BLE001
            pass
        con = connect()
        out = []
        for d in con.execute("SELECT id,title,icon,parent_id,schema FROM pages WHERE kind='database' AND deleted=0 "
                             "ORDER BY title COLLATE NOCASE"):
            schema = json.loads(d["schema"] or "[]")
            if not any(c.get("type") == "date" for c in schema):
                continue
            rows = [{"id": r["id"], "title": r["title"], "icon": r["icon"], "sort": r["sort"],
                     "props": json.loads(r["props"] or "{}")}
                    for r in con.execute("SELECT id,title,icon,sort,props FROM pages WHERE parent_id=? AND kind='row' "
                                         "AND deleted=0", (d["id"],))]
            out.append({"id": d["id"], "title": d["title"], "icon": d["icon"], "parent_id": d["parent_id"],
                        "kind": "database", "schema": schema, "rows": rows})
        self._json(out)

    def api_trash(self):
        con = connect()
        rows = con.execute("SELECT id,title,icon,kind,updated FROM pages WHERE deleted=2 "
                           "ORDER BY updated DESC").fetchall()
        self._json([dict(r) for r in rows])

    def api_search(self, q):
        q = q.strip()
        if not q:
            return self._json([])
        con = connect()
        like = f"%{q}%"
        rows = con.execute(
            "SELECT id,title,icon,kind,parent_id,content,props FROM pages WHERE deleted=0 AND kind!='template' AND "
            "(title LIKE ? OR content LIKE ? OR props LIKE ?) "
            "ORDER BY (title LIKE ?) DESC, updated DESC LIMIT 60",
            (like, like, like, like)).fetchall()
        out = []
        for r in rows:
            text = r["content"] or ""
            i = text.lower().find(q.lower())
            snippet = text[max(0, i - 60): i + 100].replace("\n", " ") if i >= 0 else ""
            out.append({"id": r["id"], "title": r["title"], "icon": r["icon"], "kind": r["kind"],
                        "snippet": snippet})
        self._json(out)

    def api_upload(self, page, name):
        page = re.sub(r"[^0-9a-zA-Z_-]", "", page) or "allgemein"
        name = safe_name(os.path.basename(name))
        dest_dir = os.path.join(FILES_DIR, page)
        os.makedirs(dest_dir, exist_ok=True)
        base, ext = os.path.splitext(name)
        dest, n = os.path.join(dest_dir, name), 2
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{base}-{n}{ext}")
            n += 1
        remaining = int(self.headers.get("Content-Length") or 0)
        with open(dest, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        self._json({"url": "/dateien/" + urllib.parse.quote(page) + "/" +
                    urllib.parse.quote(os.path.basename(dest)), "name": os.path.basename(dest)})

    def api_import(self):
        remaining = int(self.headers.get("Content-Length") or 0)
        fd, tmp = tempfile.mkstemp(suffix=".zip", prefix="notion-upload-")
        logs = []
        try:
            with os.fdopen(fd, "wb") as fh:
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 1 << 20))
                    if not chunk:
                        break
                    fh.write(chunk)
                    remaining -= len(chunk)
            if not zipfile.is_zipfile(tmp):
                return self._err(400, "Die Datei ist kein ZIP-Archiv")
            stats = NotionImporter(connect(), log=logs.append).run(tmp)
            self._json({"ok": True, "stats": stats, "log": logs})
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def serve(open_browser=True):
    url = f"http://{'127.0.0.1' if HOST == '0.0.0.0' else HOST}:{PORT}/"
    global HTTPD
    try:
        httpd = HTTPD = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError:
        print(f"Notion Lokal läuft auf diesem Rechner bereits: {url}")
        if open_browser and not os.environ.get("NOTION_LOKAL_NO_BROWSER"):
            webbrowser.open(url)
        return
    connect().close()
    other = acquire_lock_on_start()
    _heartbeat_loop()
    if other is None:
        _backup_loop()
    _recur_loop()
    print(f"Notion Lokal {local_version()} läuft auf {url}")
    print(f"Daten liegen in: {DATA_DIR}")
    if other:
        print(f"ACHTUNG: Die App ist noch auf „{other['host']}“ geöffnet (letzte Aktivität vor "
              f"{other['age'] // 60} Min). Dort beenden und Dropbox fertig synchronisieren lassen.")
    for f in conflict_files():
        print("ACHTUNG: Konfliktkopie im Datenordner:", f)
    print("Beenden mit Strg+C oder Fenster schließen")

    def stop(*_):
        raise KeyboardInterrupt
    for sig in ("SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, sig):
            try:
                signal.signal(getattr(signal, sig), stop)
            except (ValueError, OSError):
                pass
    if open_browser and not os.environ.get("NOTION_LOKAL_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        release_lock()
        httpd.server_close()
    if RESTART:
        print("Update eingespielt – starte neu …")
        env = dict(os.environ, NOTION_LOKAL_NO_BROWSER="1")
        sys.exit(subprocess.call([sys.executable, os.path.abspath(__file__)], env=env))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "serve"
    if cmd == "serve":
        serve()
    elif cmd == "import":
        if len(argv) < 3:
            sys.exit("Aufruf: python3 app.py import <Notion-Export.zip oder Ordner> [...]")
        for src in argv[2:]:
            NotionImporter(connect()).run(src)
    elif cmd == "export":
        dest = argv[2] if len(argv) > 2 else os.path.join(BASE_DIR, f"export_{dt.date.today()}")
        n = export_all(connect(), dest)
        print(f"{n} Seiten nach {dest} exportiert.")
    elif cmd == "backup":
        print("Sicherung erstellt:", backup())
    elif cmd == "passwort-entfernen":
        auth_set("")
        print("Passwortschutz aufgehoben. In der App kannst du unter „Passwort“ ein neues setzen.")
    elif cmd == "update":
        res = apply_update(force="--trotzdem" in argv)
        if res.get("blocked"):
            sys.exit("Nicht aktualisiert: Diese Programmdateien wurden lokal geändert und würden überschrieben:\n  "
                     + "\n  ".join(res["modified"]) + "\nMit  python3 app.py update --trotzdem  trotzdem einspielen. "
                     "Die bisherigen Dateien landen in alte-versionen/.")
        print(f"Aktualisiert auf {res['version']}. Vorherige Dateien: {res['backup']}" if res["updated"]
              else f"Bereits aktuell ({res['version']}).")
    elif cmd == "pruefsummen":
        for rel, s in write_checksums().items():
            print(s[:12], rel)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
