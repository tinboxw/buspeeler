import json
import sys
import time

from buspeeler.capture import CaptureManager
from buspeeler.service import Service
from buspeeler.store import Store


def test_capture_raw_chunks_serial_and_stop(tmp_path,monkeypatch):
    class ReadOnlySerial:
        def __init__(self):self.sent=False
        def open(self):pass
        def close(self):pass
        def read(self,n):
            if not self.sent:
                self.sent=True
                fields=["#"]*39;fields[6]="R25"
                return ("$OBD-RT,"+",".join(fields)+"\r\n").encode()
            time.sleep(.01);return b""
        def write(self,*args):raise AssertionError("Serial must not transmit")
    monkeypatch.setattr("buspeeler.capture.serial.Serial",ReadOnlySerial)
    store=Store(tmp_path);service=Service(store)
    project=store.create("project",{"name":"capture-test","scope":{"vehicle":"VA3","year":2023,"transmission":"MT","bus":"test","version":"test"}})
    manager=CaptureManager(store,service,tmp_path/"unused")
    script="import json,sys; print(json.dumps(dict(timestamp=42,can_id=912,data='0000010000000000')),flush=True); sys.stdin.readline()"
    monkeypatch.setattr(manager,"command",lambda config:[sys.executable,"-u","-c",script])
    config={"backend":"zlg","index":0,"channel":0,"serial_port":"fake","baudrate":115200,"bytesize":8,"parity":"N","stopbits":1,"est_origin":"est-fixture"}
    try:
        record=manager.start(project["id"],config)
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            latest=store.get(record["id"])
            if latest["count"] and latest.get("latest_reference"):break
            time.sleep(.05)
        assert latest["count"]==1
        manager.stop(record["id"])
        manager.close()
        final=store.get(record["id"])
        assert final["status"]=="completed",final
        session=store.get(final["sessions"][0])
        assert session["capture_id"]==record["id"]
        frame=store.frames(session["id"])[0]
        assert frame.time_source=="host_monotonic_receive"
        assert frame.timestamp<5
        raw=[a for a in final["artifacts"] if a["name"].startswith("can-wire")][0]
        assert json.loads(store.read_artifact(raw))["timestamp"]==42
        reference=store.list("reference",project["id"])[0]
        assert next(r for r in reference["rows"] if r["field"]=="steering")["value"]==25
        assert any(a["name"].startswith("serial-bytes") for a in final["artifacts"])
    finally:
        manager.close();store.db.close()
