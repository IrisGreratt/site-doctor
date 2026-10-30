"""生产环境启动入口。

为什么需要这个文件？
云平台（Render / Railway / Fly 等）不会直接执行 `uvicorn app.main:app`，
而是执行一个启动脚本，并且会通过环境变量 PORT 动态指定端口。

如果直接把端口写死成 8000，平台把流量转发到它分配的端口时就会 502。
所以这里读取 PORT 环境变量，并把监听地址设为 0.0.0.0（而不是 127.0.0.1，
后者只允许本机访问，外部进不来）。

本地开发仍然推荐用 `python -m uvicorn app.main:app --reload`，
这个文件主要给云平台用。
"""

import os

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        # 云平台一般不做 websocket，关闭能省点资源
        ws="none",
        # 平台日志需要实时输出，别开 access_log 之外的额外缓冲
        log_level="info",
    )
