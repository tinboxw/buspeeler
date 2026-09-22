import argparse
import json
import multiprocessing
import os
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app


def main():
    # Redirected Windows streams may use cp1252; application logs are UTF-8.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace", line_buffering=True)
    multiprocessing.freeze_support()
    parser=argparse.ArgumentParser(description="Buspeeler 本机工作台")
    parser.add_argument("--data-dir",type=Path,default=Path(os.getenv("LOCALAPPDATA",Path.home()/".local/share"))/"Buspeeler")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--no-browser",action="store_true")
    args=parser.parse_args()
    args.data_dir.mkdir(parents=True,exist_ok=True)
    lock=(args.data_dir/"instance.lock").open("a+b")
    lock.seek(0); lock.write(b"0"); lock.flush(); lock.seek(0)
    instance=args.data_dir/"instance.json"
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        if instance.exists() and not args.no_browser:
            info=json.loads(instance.read_text())
            webbrowser.open(info["url"])
        print("Buspeeler 已运行")
        return
    secret=secrets.token_urlsafe(32)
    host=f"127.0.0.1:{args.port}"
    url=f"http://{host}/#token={secret}"
    listener=socket.socket()
    try:
        listener.bind(("127.0.0.1",args.port))
    except OSError as exc:
        raise SystemExit("端口被占用，请关闭占用程序或使用 --port 指定端口") from exc
    instance.write_text(json.dumps({"url":url}),encoding="utf-8")
    if os.name!="nt": instance.chmod(0o600)
    def open_when_ready():
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"http://{host}/api/health",timeout=0.5) as response:
                    if response.status==200:
                        webbrowser.open(url); return
            except OSError: time.sleep(0.1)
    if not args.no_browser: threading.Thread(target=open_when_ready,daemon=True).start()
    print(f"Buspeeler 本机服务：http://{host}，数据目录：{args.data_dir}")
    app=create_app(args.data_dir,secret,host)
    try:
        uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=args.port,access_log=False)).run(sockets=[listener])
    finally:
        listener.close()
        instance.unlink(missing_ok=True)
        lock.close()


if __name__=="__main__": main()
