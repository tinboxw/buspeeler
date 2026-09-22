import math
import re

import cantools
from cantools.database.can import Database, Message, Signal
from cantools.database.conversion import BaseConversion

from .models import Candidate, decode


def import_dbc(text, origin, channel="can0"):
    db = cantools.database.load_string(text, database_format="dbc", strict=True)
    candidates, unsupported = [], []
    for message in db.messages:
        for signal in message.signals:
            if signal.is_float or signal.is_multiplexer or signal.multiplexer_signal:
                unsupported.append(f"{message.name}.{signal.name}: 浮点/复用需人工建立内部条件")
                continue
            candidates.append(Candidate(name=signal.name, channel=channel, can_id=message.frame_id,
                extended=message.is_extended_frame, fd=message.is_fd, message_length=message.length,
                layout={"start": signal.start, "length": signal.length, "endian": signal.byte_order, "signed": signal.is_signed},
                factor=signal.scale, offset=signal.offset, unit=signal.unit or "", origins=[origin],
                evidence=f"DBC {message.name}.{signal.name}; 导入不代表适用于目标车辆",
                unresolved=["适用车型和独立验证待确认"]).model_dump())
    return candidates, unsupported


def export_dbc(candidates, frames):
    groups, channels = {}, set()
    for candidate in candidates:
        if candidate.postprocess != "none":
            raise ValueError(f"{candidate.name}: 含整数/条件后处理，不能无损导出线性 DBC")
        channels.add(candidate.channel)
        key = (candidate.can_id, candidate.extended, candidate.fd, candidate.message_length)
        groups.setdefault(key, []).append(candidate)
    if len(channels) != 1:
        raise ValueError("每个 DBC 仅导出一条总线")
    if len({(key[0], key[1]) for key in groups}) != len(groups):
        raise ValueError("同 ID 帧格式或长度冲突，需拆分协议")
    messages = []
    for (ident, extended, fd, length), group in groups.items():
        if len({c.name for c in group})!=len(group):raise ValueError("同一报文的信号名称不能重复")
        signals, selector_layout = [], None
        for candidate in group:
            mux = candidate.mux
            if mux:
                if selector_layout and selector_layout != mux.selector:
                    raise ValueError("一个报文只支持一个已确认选择器")
                if selector_layout is None:
                    selector_layout = mux.selector
                    signals.append(Signal(name="BuspeelerMux", start=selector_layout.start,
                        length=selector_layout.length, byte_order=selector_layout.endian, is_multiplexer=True))
            signals.append(Signal(name=candidate.name, start=candidate.layout.start,
                length=candidate.layout.length, byte_order=candidate.layout.endian,
                is_signed=candidate.layout.signed, unit=candidate.unit,
                conversion=BaseConversion.factory(candidate.factor, candidate.offset),
                multiplexer_signal="BuspeelerMux" if mux else None,
                multiplexer_ids=[mux.value] if mux else None,
                comment="Validity, invalid codes and freshness are defined in the accompanying manifest."))
        messages.append(Message(frame_id=ident, name=f"Message_{ident:X}_{'E' if extended else 'S'}",
                                length=length, signals=signals, is_extended_frame=extended, is_fd=fd, strict=True))
    db = Database(messages=messages, strict=True)
    text = db.as_dbc_string()
    restored = cantools.database.load_string(text, database_format="dbc", strict=True)
    checks = 0
    counts={(c.can_id,c.extended,c.name):0 for c in candidates}
    for frame in frames:
        for candidate in candidates:
            expected = decode(frame, candidate)
            if expected is None:
                continue
            message = next(m for m in restored.messages if m.frame_id == frame.can_id and m.is_extended_frame == frame.extended)
            actual = message.decode(bytes.fromhex(frame.data), decode_choices=False)[candidate.name]
            equal=expected==actual if isinstance(expected,int) and isinstance(actual,int) else math.isclose(expected,actual,abs_tol=1e-8,rel_tol=1e-9)
            if not equal:
                raise ValueError("DBC 往返解码不一致")
            checks += 1
            counts[(candidate.can_id,candidate.extended,candidate.name)]+=1
    if not checks or any(n==0 for n in counts.values()):
        raise ValueError("部分信号没有可用于 DBC 往返验证的帧")
    return text, checks
