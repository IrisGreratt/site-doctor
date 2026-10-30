"""测试公用夹具。

为什么不用 pytest 自带的 tmp_path？
本机系统临时目录存在写入限制（PermissionError），tmp_path 无法使用。
所以这里改用项目内的 data/ 目录 + UUID 唯一文件名，
用完立刻删除，既能隔离各个测试，又不依赖系统临时目录。
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage


@pytest.fixture()
def db():
    """为每个测试准备一个独立、干净的数据库文件。"""
    db_dir = Path(__file__).resolve().parent.parent / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    path = db_dir / f"test_{uuid.uuid4().hex}.db"
    storage.init_db(path)
    try:
        yield path
    finally:
        # 连接都已关闭，可以直接删；删不掉也不影响测试结果
        try:
            path.unlink()
        except OSError:
            pass
