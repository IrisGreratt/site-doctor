"""存储层：用标准库 sqlite3 保存诊断历史与监控列表。

设计说明：
- sqlite3 是 Python 标准库，不引入额外依赖，适合小项目；
- 连接按需打开（每次操作一个连接），避免多线程共享连接引发的问题；
- 表结构在 init_db() 里用 CREATE TABLE IF NOT EXISTS 幂等创建，
  应用启动时调用一次即可。
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# 数据库放在项目 data/ 目录下，便于整体备份/删除
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "site_doctor.db"


@contextmanager
def get_conn(db_path: Path | str = DB_PATH):
    """打开一个连接，用完自动提交并关闭。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row  # 让查询结果能按列名取值
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str = DB_PATH) -> None:
    """建表（幂等）。应用启动时调用。"""
    with get_conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS checks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    NOT NULL,
                score       INTEGER NOT NULL,
                grade       TEXT    NOT NULL,
                report_json TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_checks_url
                ON checks (url, created_at DESC);

            CREATE TABLE IF NOT EXISTS monitors (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                url           TEXT    NOT NULL UNIQUE,
                interval_min  INTEGER NOT NULL DEFAULT 60,
                created_at    TEXT    NOT NULL,
                last_checked  TEXT,
                last_score    INTEGER,
                last_error    TEXT
            );
            """
        )


def _now() -> str:
    """统一的时间格式：秒级 ISO 字符串，便于排序和展示。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------- 诊断历史 ----------------

def save_check(url: str, score: int, grade: str, report: dict, db_path: Path | str = DB_PATH) -> int:
    """保存一次诊断结果，返回新记录 id。"""
    with get_conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO checks (url, score, grade, report_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (url, score, grade, json.dumps(report, ensure_ascii=False), _now()),
        )
        return cur.lastrowid


def list_history(url: str | None = None, limit: int = 50, db_path: Path | str = DB_PATH) -> list[dict]:
    """查询历史记录（不含完整 report），按时间倒序。

    url 为空时返回全部记录。
    """
    sql = "SELECT id, url, score, grade, created_at FROM checks"
    params: list = []
    if url:
        sql += " WHERE url = ?"
        params.append(url)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)

    with get_conn(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_history_item(item_id: int, db_path: Path | str = DB_PATH) -> dict | None:
    """按 id 取一条完整记录（含解析后的 report）。"""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT * FROM checks WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["report"] = json.loads(data.pop("report_json"))
    return data


def total_checks(db_path: Path | str = DB_PATH) -> int:
    """已保存的诊断总数，首页展示用。"""
    with get_conn(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM checks").fetchone()
    return int(row["n"])


def latest_check(url: str, db_path: Path | str = DB_PATH) -> dict | None:
    """取某个网址最新的一条完整记录（含 report），没有则 None。"""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM checks WHERE url = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (url,),
        ).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["report"] = json.loads(data.pop("report_json"))
    return data


# ---------------- 监控列表 ----------------

MIN_INTERVAL_MIN = 5


def add_monitor(url: str, interval_min: int = 60, db_path: Path | str = DB_PATH) -> int:
    """添加监控项。已存在时直接返回原 id 并更新间隔。"""
    interval_min = max(MIN_INTERVAL_MIN, int(interval_min))
    with get_conn(db_path) as conn:
        existing = conn.execute("SELECT id FROM monitors WHERE url = ?", (url,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE monitors SET interval_min = ? WHERE id = ?",
                (interval_min, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            "INSERT INTO monitors (url, interval_min, created_at) VALUES (?, ?, ?)",
            (url, interval_min, _now()),
        )
        return cur.lastrowid


def list_monitors(db_path: Path | str = DB_PATH) -> list[dict]:
    """所有监控项，按创建时间倒序。"""
    with get_conn(db_path) as conn:
        rows = conn.execute("SELECT * FROM monitors ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def remove_monitor(monitor_id: int, db_path: Path | str = DB_PATH) -> bool:
    """删除监控项，返回是否真的删掉了。"""
    with get_conn(db_path) as conn:
        cur = conn.execute("DELETE FROM monitors WHERE id = ?", (monitor_id,))
        return cur.rowcount > 0


def update_monitor_result(
    monitor_id: int,
    score: int | None,
    error: str | None = None,
    db_path: Path | str = DB_PATH,
) -> None:
    """自动检查后回写监控项的最新状态。"""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE monitors SET last_checked = ?, last_score = ?, last_error = ? WHERE id = ?",
            (_now(), score, error, monitor_id),
        )


def due_monitors(now: datetime | None = None, grace_seconds: int = 30, db_path: Path | str = DB_PATH) -> list[dict]:
    """返回「当前应该检查」的监控项。

    判断方式：上次检查时间 + 间隔（分钟）<= 现在。
    grace_seconds 是容差，避免定时循环刚睡醒时因为几秒误差漏掉一项。
    """
    now = now or datetime.now()
    result = []
    for item in list_monitors(db_path):
        if not item["last_checked"]:
            result.append(item)
            continue
        last = datetime.strptime(item["last_checked"], "%Y-%m-%d %H:%M:%S")
        elapsed = (now - last).total_seconds()
        if elapsed + grace_seconds >= item["interval_min"] * 60:
            result.append(item)
    return result
