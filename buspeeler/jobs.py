import concurrent.futures
import json
import multiprocessing

from .analysis import statistics, fit, search, series
from .models import Candidate, Frame
from .protocols import isotp


def compute(kind, payload):
    frames = [Frame.model_validate(f) for f in payload["frames"]]
    if kind == "statistics":
        return {"messages": statistics(frames)}
    if kind == "isotp":
        return {"messages": isotp(frames), "addressing": "normal", "warning": "仅在确认普通寻址的所选报文上使用"}
    if kind == "fit":
        candidate = Candidate.model_validate(payload["candidate"])
        # Fit raw extraction, not already scaled physical values.
        if candidate.postprocess != "none":
            raise ValueError("固件后处理候选需先建立独立的线性候选后拟合")
        candidate.factor, candidate.offset = 1, 0
        return fit(series(frames,candidate), payload["references"], payload["meta"], candidate.freshness)
    if kind == "search":
        return search(frames, payload["references"], payload["meta"], payload["message"], payload.get("max_length",16))
    raise ValueError("未知分析任务")


class Jobs:
    def __init__(self, store):
        self.store = store
        self.pool = concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
        self.pending = {}

    def submit(self, project, kind, payload, inputs):
        if len(self.pending) >= 4:
            raise ValueError("已有 4 个分析任务，请等待完成")
        row = self.store.create("job", {"project": project, "kind": kind, "status": "queued", "inputs": inputs}, project)
        self.store.update(row["id"], {"status": "running"})
        future = self.pool.submit(compute, kind, payload)
        self.pending[row["id"]] = future
        def finish(done):
            try:
                result = done.result()
                self.store.update(row["id"], {"status": "completed", "result": result})
            except Exception as exc:
                self.store.update(row["id"], {"status": "failed", "error": str(exc)})
            finally:
                self.pending.pop(row["id"], None)
        future.add_done_callback(finish)
        return row

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
