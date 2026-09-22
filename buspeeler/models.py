"""Version 1 domain contracts. DBC is an output, not the source of truth."""
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Scope(Model):
    vehicle: str = Field(min_length=1, max_length=120)
    year: int = Field(ge=1980, le=2100)
    transmission: str = Field(min_length=1, max_length=40)
    bus: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=120)


class Project(Model):
    name: str = Field(min_length=1, max_length=120)
    scope: Scope


class Frame(Model):
    timestamp: float = Field(ge=0)
    channel: str = Field(default="can0", min_length=1, max_length=80)
    can_id: int = Field(ge=0, le=0x1fffffff)
    extended: bool = False
    fd: bool = False
    brs: bool = False
    esi: bool = False
    remote: bool = False
    error: bool = False
    dlc: int | None = Field(default=None, ge=0, le=15)
    data: str = ""
    time_source: str = "import"
    hardware_timestamp: float | None = None
    hardware_ticks: int | None = Field(default=None, ge=0)
    dropped: int | None = Field(default=None, ge=0)

    @field_validator("data")
    @classmethod
    def hex_data(cls, value):
        value = value.replace(" ", "").upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}){0,64}", value):
            raise ValueError("data 必须是 0–64 字节十六进制")
        return value

    @model_validator(mode="after")
    def consistent(self):
        length = len(self.data) // 2
        if not self.extended and self.can_id > 0x7ff and not self.error:
            raise ValueError("标准帧 ID 超出范围")
        if length > (64 if self.fd else 8):
            raise ValueError("帧长度超出范围")
        if self.fd and length not in list(range(9)) + [12, 16, 20, 24, 32, 48, 64]:
            raise ValueError("CAN FD 长度无效")
        if (self.brs or self.esi) and not self.fd:
            raise ValueError("经典 CAN 不支持 BRS/ESI")
        if self.remote and (self.fd or length):
            raise ValueError("远程帧不能含数据或使用 FD")
        if self.dlc is not None and not self.error:
            expected = list(range(9)) + [12, 16, 20, 24, 32, 48, 64]
            if not self.fd and self.dlc > 8:
                raise ValueError("经典 CAN DLC 超出范围")
            if not self.remote and expected[self.dlc] != length:
                raise ValueError("DLC 与数据长度不一致")
        return self


class Layout(Model):
    start: int = Field(ge=0, le=511)
    length: int = Field(ge=1, le=64)
    endian: Literal["little_endian", "big_endian"] = "little_endian"
    signed: bool = False

    def bits(self):
        pos, result = self.start, []
        for _ in range(self.length):
            result.append(pos)
            pos = pos + 1 if self.endian == "little_endian" else (pos - 1 if pos % 8 else pos + 15)
        return result


class Mux(Model):
    selector: Layout
    value: int = Field(ge=0)


class Candidate(Model):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    channel: str = "can0"
    can_id: int = Field(ge=0, le=0x1fffffff)
    extended: bool = False
    fd: bool = False
    message_length: int = Field(ge=1, le=64)
    layout: Layout
    factor: float = 1
    offset: float = 0
    unit: str = Field(default="", max_length=30)
    semantic: str = Field(default="", max_length=80)
    kind: Literal["physical", "counter", "checksum", "unknown"] = "physical"
    mux: Mux | None = None
    invalid_raw: list[int] = Field(default_factory=list, max_length=256)
    postprocess: Literal["none", "floor", "va3_speed", "va3_steering", "va3_indicator_hold"] = "none"
    origins: list[str] = Field(min_length=1, max_length=30)
    source_sessions: list[str] = Field(default_factory=list)
    evidence: str = Field(default="", max_length=16000)
    unresolved: list[str] = Field(default_factory=list)
    lag: float = Field(default=0, ge=-30, le=30)
    freshness: float = Field(default=0.5, gt=0, le=60)

    @model_validator(mode="after")
    def check_layout(self):
        Frame(timestamp=0, can_id=self.can_id, extended=self.extended, fd=self.fd,
              data="00" * self.message_length)
        if not self.factor or max(self.layout.bits()) >= self.message_length * 8:
            raise ValueError("缩放不能为零，位布局必须位于报文内")
        if self.mux:
            bits = self.mux.selector.bits()
            if max(bits) >= self.message_length * 8 or set(bits) & set(self.layout.bits()):
                raise ValueError("复用选择器越界或与信号重叠")
            if self.mux.selector.signed or self.mux.value >= 2 ** self.mux.selector.length:
                raise ValueError("复用值超出无符号选择器范围")
        if self.postprocess=="va3_indicator_hold" and (self.layout.length!=1 or self.factor!=1 or self.offset!=0):
            raise ValueError("VA3 转向灯保持规则必须作用于未缩放的 1 位字段")
        return self


class Reference(Model):
    timestamp: float = Field(ge=0)
    value: float | None = None
    field: str = Field(min_length=1, max_length=80)
    quality: Literal["valid", "unknown", "unsupported", "excluded"] = "valid"
    direction: str | None = None
    expected_valid: bool | None = None
    raw: str = ""


class ReferenceMeta(Model):
    source: Literal["est_serial", "observation", "instrument", "demo"]
    origin: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="", max_length=30)
    accuracy: float = Field(ge=0)
    time_uncertainty: float = Field(ge=0)
    clock_scale: float = Field(default=1, gt=0.9, lt=1.1)
    clock_offset: float = 0
    description: str = Field(min_length=1, max_length=4000)


class ValidationPlan(Model):
    candidate_id: str
    session_id: str
    reference_id: str
    field: str
    max_error: float = Field(ge=0)
    min_samples: int = Field(default=20, ge=3, le=1000000)
    range_min: float
    range_max: float
    coverage: list[Literal["positive", "negative", "invalid", "timeout"]]
    observation: str = Field(min_length=10, max_length=6000)

    @model_validator(mode="after")
    def range_valid(self):
        if self.range_min >= self.range_max:
            raise ValueError("验证范围必须递增")
        return self


def extract(data: bytes, layout: Layout) -> int:
    bits = layout.bits()
    if max(bits) >= len(data) * 8:
        raise ValueError("数据长度不足")
    raw = 0
    for i, pos in enumerate(bits):
        bit = (data[pos // 8] >> (pos % 8)) & 1
        if layout.endian == "little_endian":
            raw |= bit << i
        else:
            raw = (raw << 1) | bit
    if layout.signed and raw >= 1 << (layout.length - 1):
        raw -= 1 << layout.length
    return raw


def decode(frame: Frame, candidate: Candidate):
    if (frame.error or frame.remote or frame.can_id != candidate.can_id or
        frame.channel != candidate.channel or frame.extended != candidate.extended or
        frame.fd != candidate.fd or len(frame.data) != 2 * candidate.message_length):
        return None
    data = bytes.fromhex(frame.data)
    if candidate.mux and extract(data, candidate.mux.selector) != candidate.mux.value:
        return None
    raw = extract(data, candidate.layout)
    if raw in candidate.invalid_raw:
        return None
    if candidate.postprocess == "va3_speed":
        return (raw >> 1) // 100
    if candidate.postprocess == "va3_steering":
        return ((raw & 0x3fff) * 100) // 0x2e00
    if candidate.postprocess == "va3_indicator_hold":
        return None  # A single frame cannot establish the retained state; use series().
    factor=int(candidate.factor) if float(candidate.factor).is_integer() else candidate.factor
    offset=int(candidate.offset) if float(candidate.offset).is_integer() else candidate.offset
    value = raw * factor + offset
    return math.floor(value) if candidate.postprocess == "floor" else value
