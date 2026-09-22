import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path

import serial
from serial.tools import list_ports

from .models import Frame
from .protocols import SerialLines, parse_est
from .store import canonical


class CaptureManager:
    def __init__(self, store, service, collector):
        self.store, self.service, self.collector = store, service, Path(collector)
        self.active = {}
        self.lock = threading.RLock()

    def devices(self):
        directory = self.store.root / "devices"
        profiles = []
        for path in directory.glob("*.json") if directory.exists() else []:
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
                profiles.append({"profile": path.stem, "model": profile.get("model"),
                                 "backend": profile.get("backend"), "status": "需核对 SDK 哈希与监听验收记录"})
            except (ValueError, OSError):
                profiles.append({"profile": path.stem, "status": "配置不可读"})
        return {"collector_available": self.collector.is_file(), "platform": platform.system(),
                "profiles": profiles, "serial": [{"port": p.device, "description": p.description} for p in list_ports.comports()],
                "backends": [{"name": "zlg", "model": "USBCAN2", "status": "未实机验收；需要型号匹配的 SDK 和监听证明"},
                             {"name": "chuangxin", "model": "CANalyst-II", "status": "未实机验收；需要型号匹配的 SDK 和监听证明"},
                             {"name": "socketcan", "status": "Linux 已配置的经典 CAN listen-only 接口"}]}

    def command(self, config):
        if not self.collector.is_file():
            raise ValueError("采集程序缺失，请使用完整发行包")
        backend = config["backend"]
        command = [str(self.collector), "backend", backend]
        if backend == "socketcan":
            if platform.system() != "Linux":
                raise ValueError("SocketCAN 仅限 Linux")
            import re
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,15}", config["interface"]):
                raise ValueError("接口名称无效")
            return command + ["interface", config["interface"]]
        if backend not in ("zlg", "chuangxin"):
            raise ValueError("未知采集后端")
        profile_id = config.get("profile", "")
        if not profile_id or not all(c.isalnum() or c in "_-" for c in profile_id):
            raise ValueError("需要有效的设备验收配置")
        path = self.store.root / "devices" / (profile_id + ".json")
        if not path.is_file():
            raise ValueError("缺少 SDK/监听验收配置；不会尝试普通模式")
        profile = json.loads(path.read_text(encoding="utf-8"))
        library = Path(profile["library"])
        if not library.is_absolute() or not library.is_file():
            raise ValueError("SDK 库路径无效")
        if (profile["backend"] != backend or profile["system"] != platform.system() or
            profile.get("abi") != "legacy-controlcan-v1" or not profile.get("listen_only_evidence") or
            not profile.get("model") or not profile.get("driver_version") or
            profile.get("library_sha256") != hashlib.sha256(library.read_bytes()).hexdigest()):
            raise ValueError("设备配置、SDK 哈希或监听证据不匹配")
        bitrate = str(config["bitrate"])
        timing = profile.get("timings", {}).get(bitrate)
        if not timing or len(timing) != 2 or not all(isinstance(v,int) and 0<=v<=255 for v in timing):
            raise ValueError("此速率未包含在厂商配置中")
        return command + ["library", str(library), "type", str(int(profile["device_type"])),
                          "index", str(config["index"]), "channel", str(config["channel"]),
                          "timing0", str(timing[0]), "timing1", str(timing[1]), "verified", "1"]

    def start(self, project, config):
        self.store.get(project, "project")
        command = self.command(config)
        with self.lock:
            device_key = (config["backend"], config.get("profile"), config.get("index"), config.get("interface"))
            if any(v["device_key"] == device_key for v in self.active.values()):
                raise ValueError("设备已被本应用占用；双通道请选择同一任务中的双通道模式")
            if config.get("serial_port") and self.active:
                raise ValueError("同一串口参考仅允许一个采集会话")
            record = self.store.create("capture", {"project": project, "config": config, "status": "running",
                "scope": self.store.get(project, "project")["scope"], "count": 0, "error": None}, project)
            state = {"stop": threading.Event(), "device_key": device_key, "thread": None, "process": None}
            self.active[record["id"]] = state
            thread = threading.Thread(target=self.run, args=(record, command, state), daemon=True)
            state["thread"] = thread
            thread.start()
        return record

    def stop(self, ident):
        with self.lock:
            state = self.active.get(ident)
            if not state:
                raise ValueError("采集未运行")
            self.store.update(ident, {"status": "stopping"})
            state["stop"].set()
        return {"status": "stopping"}

    def run(self, record, command, state):
        root = self.store.root / "captures" / record["id"]
        root.mkdir(parents=True)
        config = record["config"]
        start = time.monotonic()
        errors, count, chunks, reference_chunks = [], [0], [], []
        def save_lines(stream, prefix, parse):
            path, writer, wire, rows, chunk = None, None, None, 0, 0
            try:
                for raw in stream:
                    if not writer or rows >= 10000:
                        if writer: writer.close()
                        if wire: wire.close()
                        path = root / f"{prefix}-{chunk:06d}.jsonl"
                        writer = path.open("x", encoding="utf-8", buffering=1)
                        if prefix=="can":wire=(root/f"can-wire-{chunk:06d}.jsonl").open("x",encoding="utf-8",buffering=1)
                        (chunks if prefix == "can" else reference_chunks).append(path)
                        rows, chunk = 0, chunk+1
                    if rows%100==0 and shutil.disk_usage(root).free < 100*1024*1024:
                        raise OSError("磁盘空间低于 100 MiB")
                    if wire:wire.write(raw)
                    item = parse(raw)
                    writer.write(canonical(item) + "\n")
                    if prefix=="can":state["latest_frame"]=item
                    else:state["latest_reference"]=parse_est(item["raw"],item["timestamp"])
                    rows += 1
                    count[0] += 1 if prefix == "can" else 0
            except Exception as exc:
                errors.append(str(exc)); state["stop"].set()
            finally:
                if writer: writer.close()
                if wire: wire.close()

        def serial_read():
            port = serial.Serial()
            port.port, port.baudrate, port.timeout = config["serial_port"], config["baudrate"], 0.2
            port.bytesize, port.parity, port.stopbits = config["bytesize"], config["parity"], config["stopbits"]
            port.rtscts = port.dsrdtr = port.xonxoff = False
            port.dtr = port.rts = False
            parser = SerialLines()
            raw_file=None;blocks=0
            try:
                port.open()
                while not state["stop"].is_set():
                    data = port.read(4096)
                    if not data: continue
                    if blocks%10000==0:
                        if raw_file:raw_file.close()
                        raw_file=(root/f"serial-bytes-{blocks//10000:06d}.jsonl").open("x",encoding="utf-8",buffering=1)
                    timestamp = time.monotonic()-start
                    raw_file.write(canonical({"timestamp": timestamp, "base64": base64.b64encode(data).decode()})+"\n")
                    blocks+=1
                    for line in parser.feed(data):
                        yield {"timestamp": timestamp, "raw": line}
            finally:
                if raw_file:raw_file.close()
                port.close()

        try:
            log = (root / "collector.log").open("x", encoding="utf-8")
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            state["process"] = process
            def frame(raw):
                # Both streams use host monotonic receipt; collector's wall time stays in can-wire chunks.
                item = Frame.model_validate_json(raw)
                item.timestamp, item.time_source = time.monotonic()-start, "host_monotonic_receive"
                return item.model_dump()
            threads = [threading.Thread(target=save_lines, args=(process.stdout,"can",frame), daemon=True)]
            if config.get("serial_port"):
                threads.append(threading.Thread(target=save_lines, args=(serial_read(),"est",lambda x:x), daemon=True))
            for thread in threads: thread.start()
            while process.poll() is None and not state["stop"].wait(0.25):
                self.store.update(record["id"], {"count": count[0],"latest_frame":state.get("latest_frame"),
                    "latest_reference":state.get("latest_reference")})
            if process.poll() is None:
                try: process.stdin.write("stop\n"); process.stdin.flush()
                except (OSError, BrokenPipeError): pass
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(); errors.append("采集器未及时退出，已终止")
            elif process.returncode:
                errors.append(f"采集器退出码 {process.returncode}；请查看采集日志")
            state["stop"].set()
            for thread in threads: thread.join(timeout=3)
            log.close()
            sessions = []
            for path in chunks:
                session = self.service.import_can(record["project"], path.name, path.read_bytes())
                self.store.update(session["id"], {"capture_id":record["id"]})
                sessions.append(session["id"])
                if reference_chunks:
                    data = b"".join(p.read_bytes() for p in reference_chunks)
                    self.service.import_reference(record["project"], session["id"], "EST 同步参考", data,
                        {"source": "est_serial", "origin": config.get("est_origin", "est-unknown"),
                         "unit": "", "accuracy": 0, "time_uncertainty": 0.2,
                         "description": "EST 周期快照；固件/精度/时间不确定度须实测，默认数值非验收证据"}, est=True)
            artifacts = [{"name": p.name, **self.store.artifact(p.read_bytes(), ".capture.raw")} for p in root.iterdir() if p.is_file()]
            self.store.update(record["id"], {"status": "failed" if errors else "completed", "count": count[0],
                "error": "; ".join(errors) or None, "sessions": sessions, "artifacts": artifacts})
        except Exception as exc:
            state["stop"].set()
            self.store.update(record["id"], {"status": "failed", "error": str(exc), "count": count[0],
                                         "recovery": str(root)})
        finally:
            process=state.get("process")
            if process and process.poll() is None:
                process.kill();process.wait()
            with self.lock: self.active.pop(record["id"], None)

    def close(self):
        with self.lock:
            states = list(self.active.values())
            for state in states: state["stop"].set()
        for state in states:
            state["thread"].join(timeout=10)
