import math
from collections import defaultdict

import numpy as np
from scipy.stats import linregress

from .models import Candidate, Frame, Layout, decode, extract


def identity(frame):
    return f"{frame.channel}:{frame.can_id:X}:{int(frame.extended)}:{int(frame.fd)}:{len(frame.data)//2}"


def statistics(frames):
    groups = defaultdict(list)
    for frame in frames:
        if not frame.error and not frame.remote:
            groups[identity(frame)].append(frame)
    output = []
    for key, group in groups.items():
        group.sort(key=lambda x: x.timestamp)
        length = len(group[0].data) // 2
        array = np.array([list(bytes.fromhex(f.data)) for f in group], dtype=np.uint8).reshape(len(group), length)
        bits = np.unpackbits(array, axis=1, bitorder="little")
        p = bits.mean(axis=0)
        safe = np.clip(p, 1e-15, 1-1e-15)
        entropy = -(safe*np.log2(safe)+(1-safe)*np.log2(1-safe))
        intervals = np.diff([f.timestamp for f in group])
        flip = (bits[1:] != bits[:-1]).mean(axis=0).tolist() if len(group) > 1 else [None] * (length * 8)
        counters = []
        for byte in range(length):
            if len(group) > 3:
                ratio = float(np.mean((np.diff(array[:, byte].astype(int)) % 256) == 1))
                if ratio > 0.8:
                    counters.append({"start": byte*8, "length": 8, "increment_ratio": ratio, "status": "candidate"})
        output.append({"key": key, "channel": group[0].channel, "can_id": group[0].can_id,
                       "extended": group[0].extended, "fd": group[0].fd, "length": length,
                       "count": len(group), "period": float(np.median(intervals)) if len(intervals) else None,
                       "jitter": float(np.std(intervals)) if len(intervals) else None,
                       "ones": p.tolist(), "entropy": entropy.tolist(), "flips": flip,
                       "counter_candidates": counters})
    return sorted(output, key=lambda x: (x["channel"], x["can_id"]))


def series(frames, candidate):
    # Retain invalid entries: never hold a value across a known invalid frame.
    if candidate.postprocess=="va3_indicator_hold":
        raw_candidate=candidate.model_copy(update={"postprocess":"none"})
        result=[]; state=None; zeros=0; previous=None
        for t,value in series(frames,raw_candidate):
            if previous is not None and t-previous>candidate.freshness:
                state=None;zeros=0
            if value is None:
                state=None;zeros=0
            elif value:
                state=1;zeros=0
            else:
                zeros+=1
                if zeros>=5:state=0;zeros=0
            result.append((t,state));previous=t
        return result
    return [(f.timestamp, decode(f, candidate)) for f in frames
            if f.channel == candidate.channel and f.can_id == candidate.can_id and
            f.extended == candidate.extended and f.fd == candidate.fd and not f.remote]


def align(samples, references, lag, freshness, meta):
    samples = sorted(samples)
    times = np.array([t for t, _ in samples])
    x, y, matched, missing = [], [], [], 0
    for ref in references:
        if ref["quality"] != "valid" or ref.get("value") is None:
            continue
        t = ref["timestamp"] * meta.get("clock_scale", 1) + meta.get("clock_offset", 0) - lag
        i = int(np.searchsorted(times, t, side="right")) - 1
        if i < 0 or t-times[i] > freshness or samples[i][1] is None:
            missing += 1
            continue
        x.append(samples[i][1]); y.append(ref["value"]); matched.append(ref["timestamp"])
    return np.array(x), np.array(y), matched, missing


def fit(samples, references, meta, freshness, lag_min=-1.0, lag_max=1.0, lag_step=0.05):
    results = []
    for lag in np.arange(lag_min, lag_max + lag_step/2, lag_step):
        x, y, _, missing = align(samples, references, float(lag), freshness, meta)
        if len(x) < 5 or len(np.unique(x)) < 3 or len(np.unique(y)) < 3:
            continue
        regression = linregress(x, y)
        if abs(regression.slope) < 1e-15:
            continue
        errors = x * regression.slope + regression.intercept - y
        results.append({"factor": float(regression.slope), "offset": float(regression.intercept),
                        "lag": float(lag), "rmse": float(np.sqrt(np.mean(errors**2))),
                        "max_error": float(np.max(np.abs(errors))), "samples": len(x),
                        "missing": missing, "correlation": float(regression.rvalue),
                        "range": [float(np.min(y)), float(np.max(y))]})
    if not results:
        raise ValueError("有效动态样本不足，无法辨识 factor/offset")
    # Missing overlap is penalized, so a tiny coincident interval cannot win.
    results.sort(key=lambda r: (r["missing"]/(r["missing"]+r["samples"]), r["rmse"]))
    return {"best": results[0], "alternatives": results[1:6], "status": "candidate",
            "warning": "相关与拟合仅用于排序，不证明语义；验证需使用独立会话"}


def search(frames, references, meta, message, max_length=16, limit=20):
    group = [f for f in frames if identity(f) == message]
    if len(group) < 5:
        raise ValueError("报文样本不足")
    if len(group)>10000:
        raise ValueError("候选搜索单次最多 1 万帧，请缩小页面时间窗口；未丢弃或抽样原始帧")
    # Explicit v1 bound: exhaustive search within one selected message, <= 32 bits.
    max_length = min(max_length, 32)
    results = []
    first = group[0]
    for endian in ("little_endian", "big_endian"):
        for start in range(len(first.data)*4):
            for length in range(1, max_length+1):
                for signed in (False, True):
                    layout = Layout(start=start, length=length, endian=endian, signed=signed)
                    if max(layout.bits()) >= len(first.data)*4:
                        continue
                    values = [(f.timestamp, extract(bytes.fromhex(f.data), layout)) for f in group]
                    x, y, _, missing = align(values, references, 0, 0.5, meta)
                    if len(x) < 5 or len(np.unique(x)) < 2 or len(np.unique(y)) < 2:
                        continue
                    regression = linregress(x, y)
                    if abs(regression.slope) < 1e-15:
                        continue
                    rmse = float(np.sqrt(np.mean((x*regression.slope+regression.intercept-y)**2)))
                    results.append({"layout": layout.model_dump(), "factor": float(regression.slope),
                                    "offset": float(regression.intercept), "rmse": rmse,
                                    "samples": len(x), "missing": missing})
    results.sort(key=lambda r: (r["missing"], r["rmse"], r["layout"]["length"]))
    return {"candidates": results[:limit], "searched": len(results),
            "warning": "边界/符号/大小端可能不可辨识。搜索使用零延迟；选定候选后联合拟合时间偏移。"}


def validate(frames, refs, candidate, meta, plan):
    x, y, times, missing = align(series(frames, candidate), refs, candidate.lag, candidate.freshness, meta)
    errors = np.abs(x-y)
    invalid = [r for r in refs if r["quality"] in ("unknown", "unsupported") and r.get("expected_valid") is False]
    invalid_checks = []
    for r in invalid:
        probe = {**r, "quality": "valid", "value": 0}
        px, _, _, _ = align(series(frames, candidate), [probe], candidate.lag, candidate.freshness, meta)
        invalid_checks.append(len(px) == 0)
    passed = (len(x) >= plan["min_samples"] and missing == 0 and
              len(errors) > 0 and float(max(errors)) <= plan["max_error"] and
              float(min(y)) <= plan["range_min"] and float(max(y)) >= plan["range_max"])
    directions = direction_checks(frames,refs,candidate,meta)
    passed = passed and (directions is None or directions["passed"])
    return {"passed_numeric": bool(passed), "direction_checks":directions, "samples": len(x), "missing": missing,
            "mae": float(np.mean(errors)) if len(errors) else None,
            "rmse": float(np.sqrt(np.mean(errors**2))) if len(errors) else None,
            "max_error": float(max(errors)) if len(errors) else None,
            "range": [float(min(y)), float(max(y))] if len(y) else None,
            "invalid_checked": len(invalid_checks), "invalid_passed": bool(invalid_checks) and all(invalid_checks),
            "timestamps": times[:1000]}


def direction_checks(frames,refs,candidate,meta):
    if candidate.postprocess!="va3_steering":return None
    selected=sorted((f for f in frames if decode(f,candidate) is not None),key=lambda f:f.timestamp)
    times=np.array([f.timestamp for f in selected])
    checked=0;errors=0
    for ref in refs:
        if ref["quality"]!="valid" or ref.get("value") is None:continue
        t=ref["timestamp"]*meta.get("clock_scale",1)+meta.get("clock_offset",0)-candidate.lag
        i=int(np.searchsorted(times,t,side="right"))-1
        if i<0 or t-times[i]>candidate.freshness:continue
        raw=extract(bytes.fromhex(selected[i].data),candidate.layout)
        expected="R" if raw>0x8000 else "L"
        checked+=1
        if ref.get("direction")!=expected:errors+=1
    return {"checked":checked,"mismatches":errors,"passed":checked>0 and errors==0,
            "note":"R/L 编码一致性；物理左右映射仍需独立操作证据"}
