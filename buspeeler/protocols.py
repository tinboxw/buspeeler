"""Passive import and reference parsing. No transport here ever transmits."""
import json
import re

from .models import Frame

EST_FIELDS = [
    "key", "voltage", "rpm", "speed", "coolant", "gear", "steering", "accelerator",
    "odometer", "fuel", "average_fuel", "trip_distance", "dtc_count", "runtime",
    "driver_door", "passenger_door", "left_rear_door", "right_rear_door", "trunk",
    "driver_window", "passenger_window", "left_rear_window", "right_rear_window",
    "sunroof", "central_lock", "driver_belt", "passenger_belt", "clutch", "horn",
    "wiper", "handbrake", "brake", "left_indicator", "right_indicator", "position_light",
    "low_beam", "high_beam", "front_fog", "rear_fog",
]
EST_UNITS = {"rpm": "rpm", "speed": "km/h", "steering": "%", "accelerator": "%",
             "voltage": "V", "coolant": "degC", "odometer": "km", "fuel": "%"}


def parse_est(line: str, timestamp: float):
    parts = line.strip().split(",")
    if parts[0] != "$OBD-RT" or len(parts) != 40:
        return {"timestamp": timestamp, "raw": line, "quality": "unknown", "fields": [],
                "reason": "未知前缀或字段数；未猜测字段映射"}
    fields = []
    for name, token in zip(EST_FIELDS, parts[1:]):
        item = {"timestamp": timestamp, "field": name, "raw": token, "value": None,
                "quality": "valid", "unit": EST_UNITS.get(name, ""), "direction": None}
        if token == "#":
            item["quality"] = "unsupported"
        elif name == "steering":
            match = re.fullmatch(r"([RL])(\d+(?:\.\d+)?)", token)
            if match:
                item.update(value=float(match[2]), direction=match[1])
                if item["value"] > 100:
                    item["warning"] = "百分比超过 100；保留原值，方向待实测"
            else:
                item["quality"] = "unknown"
        elif name in ("key", "gear"):
            allowed = {"key": ["OFF", "ACC", "ON", "START"],
                       "gear": ["MN", "M1", "M2", "M3", "M4", "M5", "MR", "AP", "AR", "AN", "AD", "AS", "AL"]}
            item["quality"] = "valid" if token in allowed[name] else "unknown"
        else:
            try:
                value = float(token)
                if not (-1e15 < value < 1e15):
                    raise ValueError()
                item["value"] = value
            except ValueError:
                item["quality"] = "unknown"
        fields.append(item)
    return {"timestamp": timestamp, "raw": line, "quality": "parsed", "fields": fields,
            "profile": "est580-rt39-v1"}


class SerialLines:
    def __init__(self, limit=8192):
        self.buffer = bytearray()
        self.limit = limit
        self.discarding = False

    def feed(self, data: bytes):
        result = []
        for b in data:
            if b in (10, 13):
                if self.buffer:
                    result.append(self.buffer.decode("ascii", errors="replace"))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                self.buffer.append(b)
                if len(self.buffer) > self.limit:
                    result.append("[oversized line]")
                    self.buffer.clear()
                    self.discarding = True
        return result


def parse_can_line(line: str) -> Frame:
    if line.lstrip().startswith("{"):
        return Frame.model_validate(json.loads(line))
    match = re.fullmatch(r"\s*\(([\d.]+)\)\s+(\S+)\s+([0-9a-fA-F]{3,8})(##?|#)([0-9a-fA-FRr]*)\s*", line)
    if not match:
        raise ValueError("仅接受 candump -L 或 Frame JSONL")
    timestamp, channel, ident, sep, payload = match.groups()
    fd, brs, esi, remote, dlc = sep == "##", False, False, False, None
    if fd:
        if not payload:
            raise ValueError("FD 标志缺失")
        flags, payload = int(payload[0], 16), payload[1:]
        if flags & ~3:
            raise ValueError("FD 标志无效")
        brs, esi = bool(flags & 1), bool(flags & 2)
    elif payload.upper().startswith("R"):
        remote, dlc, payload = True, int(payload[1:] or "0"), ""
    raw_id = int(ident, 16)
    error = bool(raw_id & 0x20000000)
    return Frame(timestamp=float(timestamp), channel=channel, can_id=raw_id & 0x1fffffff,
                 extended=len(ident) == 8 and not error, error=error, fd=fd, brs=brs,
                 esi=esi, remote=remote, dlc=dlc, data=payload)


def est_changes(rows, max_gap=0.5):
    previous, changes = {}, []
    for row in rows:
        if row["quality"] != "parsed":
            previous.clear()
            continue
        for field in row["fields"]:
            name = field["field"]
            old = previous.get(name)
            if field["quality"] != "valid":
                previous.pop(name, None)
                continue
            if old and 0 <= field["timestamp"] - old["timestamp"] <= max_gap and field["raw"] != old["raw"]:
                changes.append({"field": name, "start": old["timestamp"], "end": field["timestamp"],
                                "from": old["raw"], "to": field["raw"], "source": "est_reference"})
            previous[name] = field
    return changes


def isotp(frames, timeout=1.0):
    """Explicit normal addressing only; no inferred ID pairs, no flow-control."""
    active, output = {}, []
    for index, frame in enumerate(frames):
        if frame.error or frame.remote or not frame.data:
            continue
        key = (frame.channel, frame.can_id, frame.extended, frame.fd)
        data = bytes.fromhex(frame.data)
        old = active.get(key)
        if old and frame.timestamp - old["time"] > timeout:
            output.append({**active.pop(key), "status": "timeout"})
        kind = data[0] >> 4
        if kind == 0:
            length, start = data[0] & 15, 1
            if frame.fd and length == 0 and len(data) > 1:
                length, start = data[1], 2
            if length and length <= len(data) - start:
                output.append({"key": key, "data": data[start:start+length].hex(), "frames": [index], "status": "complete"})
        elif kind == 1 and len(data) >= 2:
            if key in active:
                output.append({**active.pop(key), "status": "interrupted"})
            length = ((data[0] & 15) << 8) | data[1]
            if length > len(data) - 2:
                active[key] = {"key": key, "length": length, "data": data[2:].hex(),
                               "next": 1, "time": frame.timestamp, "frames": [index]}
        elif kind == 2 and key in active:
            entry = active[key]
            if data[0] & 15 != entry["next"]:
                output.append({**active.pop(key), "status": "sequence_error"})
                continue
            entry["data"] += data[1:].hex()
            entry["frames"].append(index)
            entry.update(next=(entry["next"] + 1) % 16, time=frame.timestamp)
            if len(entry["data"]) >= entry["length"] * 2:
                entry["data"] = entry["data"][:entry["length"] * 2]
                output.append({**active.pop(key), "status": "complete"})
    output.extend({**entry, "status": "incomplete"} for entry in active.values())
    return output
