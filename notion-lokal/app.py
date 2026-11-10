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
"""

import csv
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
import re
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
    kind      TEXT NOT NULL DEFAULT 'page',   -- page | database | row
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
"""

_write_lock = threading.Lock()


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(FILES_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA_SQL)
    return con


def new_id():
    return uuid.uuid4().hex


def row_to_page(r, full=True):
    d = {
        "id": r["id"],
        "parent_id": r["parent_id"],
        "kind": r["kind"],
        "title": r["title"],
        "icon": r["icon"],
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
            columns.append({"name": name, "type": ctype})

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
            return f"{bang}[{label}]({self._link_for(target, base_file, owner_id)}{title})"
        text = LINK_RE.sub(repl, text)
        # HTML-<img src="…"> (kommt in Notion-Exporten gelegentlich vor)
        def img(m):
            return m.group(1) + self._link_for(m.group(2), base_file, owner_id) + m.group(3)
        return re.sub(r'(<img[^>]*?src=")([^"]+)(")', img, text)

    def _rewrite_cell(self, value, ctype, folder, base_file):
        value = (value or "").strip()
        if not value:
            return ""
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
    rows = [row_to_page(r) for r in con.execute("SELECT * FROM pages WHERE deleted=0")]
    by_parent = {}
    for p in rows:
        by_parent.setdefault(p["parent_id"], []).append(p)
    ids = {p["id"] for p in rows}
    roots = [p for p in rows if p["parent_id"] not in ids]

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
            cols = [c["name"] for c in p["schema"]]
            with open(base + ".csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                for c in children:
                    if c["kind"] == "row":
                        w.writerow([c["title"] if i == 0 else c["props"].get(col, "")
                                    for i, col in enumerate(cols)])
        head = f"# {p['title']}\n\n"
        if p["kind"] == "row":
            head += "".join(f"{k}: {v}\n" for k, v in p["props"].items() if v) + "\n"
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


# ---------------------------------------------------------------------------
# HTTP-Server + JSON-API
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "NotionLokal/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- Hilfsfunktionen ---
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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

    def _check_origin(self):
        # Nur Anfragen von der eigenen Oberfläche zulassen (Schutz vor fremden Webseiten)
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).netloc != self.headers.get("Host"):
            self._err(403, "Fremder Ursprung")
            return False
        return True

    # --- Routing ---
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        q = urllib.parse.parse_qs(url.query)
        try:
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
            if path == "/api/search":
                return self.api_search((q.get("q") or [""])[0])
            if path == "/api/trash":
                return self.api_trash()
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
        try:
            if url.path == "/api/page":
                return self.api_create(self._jbody())
            if url.path.startswith("/api/restore/"):
                return self.api_restore(url.path.split("/")[3])
            if url.path == "/api/upload":
                return self.api_upload((q.get("page") or ["allgemein"])[0], (q.get("name") or ["datei"])[0])
            if url.path == "/api/import":
                return self.api_import()
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_PUT(self):
        if not self._check_origin():
            return
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path.startswith("/api/page/"):
                return self.api_update(url.path.split("/")[3], self._jbody())
            self._err(404, "Unbekannter Pfad")
        except Exception as e:  # noqa: BLE001
            self._err(500, str(e))

    def do_DELETE(self):
        if not self._check_origin():
            return
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
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
            "SELECT id,parent_id,kind,title,icon,sort FROM pages WHERE deleted=0 AND kind!='row' "
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
                x.pop("content", None)
        if page["kind"] == "row":
            db = con.execute("SELECT schema,title FROM pages WHERE id=?", (r["parent_id"],)).fetchone()
            page["db_schema"] = json.loads(db["schema"]) if db else []
        page["children"] = [dict(x) for x in con.execute(
            "SELECT id,title,icon,kind FROM pages WHERE parent_id=? AND kind!='row' AND deleted=0 "
            "ORDER BY sort, title COLLATE NOCASE", (pid,))]
        self._json(page)

    def api_create(self, b):
        kind = b.get("kind", "page")
        if kind not in ("page", "database", "row"):
            return self._err(400, "Ungültiger Typ")
        pid, ts = new_id(), now()
        schema = b.get("schema") or ([{"name": "Name", "type": "title"},
                                      {"name": "Status", "type": "select"},
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

    def api_update(self, pid, b):
        fields, vals = [], []
        for k in ("title", "icon", "content", "parent_id", "sort"):
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
            return self._json({"ok": True})
        fields.append("updated=?")
        vals.append(now())
        with _write_lock:
            con = connect()
            if "parent_id" in b and b["parent_id"]:
                # Verhindern, dass eine Seite in sich selbst verschoben wird
                cur = b["parent_id"]
                while cur:
                    if cur == pid:
                        return self._err(400, "Eine Seite kann nicht in sich selbst verschoben werden")
                    row = con.execute("SELECT parent_id FROM pages WHERE id=?", (cur,)).fetchone()
                    cur = row["parent_id"] if row else None
            con.execute(f"UPDATE pages SET {', '.join(fields)} WHERE id=?", (*vals, pid))
            # Titel einer Datenbankzeile mit Titelspalte synchron halten
            r = con.execute("SELECT kind,parent_id,title,props FROM pages WHERE id=?", (pid,)).fetchone()
            if r and r["kind"] == "row":
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
            con.commit()
        self._json({"ok": True})

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
            "SELECT id,title,icon,kind,parent_id,content,props FROM pages WHERE deleted=0 AND "
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
    connect().close()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/"
    print(f"Notion Lokal läuft auf {url}")
    print(f"Daten liegen in: {DATA_DIR}")
    print("Beenden mit Strg+C")
    if open_browser and not os.environ.get("NOTION_LOKAL_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


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
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
