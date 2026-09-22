import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Store:
    """One application writer; raw artifacts are immutable and content hashed."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "buspeeler.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS objects (id TEXT PRIMARY KEY, kind TEXT NOT NULL, project TEXT NOT NULL, created REAL NOT NULL, body TEXT NOT NULL)")
        self.db.execute("CREATE INDEX IF NOT EXISTS objects_kind ON objects(kind,project)")
        self.db.execute("CREATE TABLE IF NOT EXISTS frames (session TEXT, seq INTEGER, time REAL, message TEXT, body TEXT, PRIMARY KEY(session,seq))")
        self.db.execute("CREATE INDEX IF NOT EXISTS frames_window ON frames(session,message,time)")
        self.db.commit()

    def create(self, kind, body, project=""):
        ident = uuid.uuid4().hex
        row = {**body, "id": ident, "created": time.time()}
        with self.lock, self.db:
            self.db.execute("INSERT INTO objects VALUES (?,?,?,?,?)", (ident, kind, project, row["created"], canonical(row)))
        return row

    def get(self, ident, kind=None):
        with self.lock:
            row = self.db.execute("SELECT kind,body FROM objects WHERE id=?", (ident,)).fetchone()
        if not row or (kind and row[0] != kind):
            raise ValueError("记录不存在或类型不匹配")
        return json.loads(row[1])

    def list(self, kind, project=None, summary=False):
        expression="json_remove(body,'$.rows','$.changes')" if summary and kind=="reference" else "body"
        sql, args = f"SELECT {expression} FROM objects WHERE kind=?", [kind]
        if project is not None:
            sql += " AND project=?"
            args.append(project)
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY created DESC", args).fetchall()
        return [json.loads(row[0]) for row in rows]

    def update(self, ident, changes):
        with self.lock, self.db:
            row = self.get(ident)
            row.update(changes)
            self.db.execute("UPDATE objects SET body=? WHERE id=?", (canonical(row), ident))
        return row

    def artifact(self, data: bytes, suffix):
        sha = hashlib.sha256(data).hexdigest()
        path = self.root / "artifacts" / (sha + suffix)
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            with path.open("xb") as stream:
                stream.write(data)
        elif hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError("已有原始文件哈希异常")
        return {"file": path.name, "sha256": sha, "bytes": len(data)}

    def read_artifact(self, item):
        name = item["file"]
        if Path(name).name != name:
            raise ValueError("非法文件路径")
        data = (self.root / "artifacts" / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("原始记录已变更，证据不可使用")
        return data

    def recover(self):
        for kind in ("job", "capture"):
            for row in self.list(kind):
                if row["status"] in ("running", "queued", "stopping"):
                    self.update(row["id"], {"status": "interrupted", "error": "应用上次未正常结束；保留原始数据，可重新导入/运行"})

    def index_frames(self, session, frames):
        from .analysis import identity
        with self.lock, self.db:
            self.db.executemany("INSERT INTO frames VALUES (?,?,?,?,?)",
                ((session, i, f.timestamp, identity(f), canonical(f.model_dump())) for i, f in enumerate(frames)))

    def frames(self, session, message=None, start=0, end=1e20, limit=1000000, offset=0):
        from .models import Frame
        sql = "SELECT body FROM frames WHERE session=? AND time>=? AND time<=?"
        args = [session, start, end]
        if message:
            sql += " AND message=?"
            args.append(message)
        with self.lock:
            rows = self.db.execute(sql + " ORDER BY time,seq LIMIT ? OFFSET ?", args+[limit, offset]).fetchall()
        return [Frame.model_validate_json(row[0]) for row in rows]
