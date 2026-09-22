import hashlib
import io
import json
import zipfile
from functools import wraps

from . import analysis
from .dbc import export_dbc, import_dbc
from .models import Candidate, Frame, Reference, ReferenceMeta, ValidationPlan
from .protocols import parse_can_line, parse_est, est_changes
from .store import canonical, digest


def serialized(method):
    @wraps(method)
    def call(self,*args,**kwargs):
        with self.store.lock:
            return method(self,*args,**kwargs)
    return call


class Service:
    def __init__(self, store):
        self.store = store

    def belongs(self, ident, kind, project):
        item = self.store.get(ident, kind)
        if item.get("project") != project:
            raise ValueError("数据不属于当前项目")
        return item

    def import_can(self, project, name, data, demo=False):
        self.store.get(project, "project")
        artifact = self.store.artifact(data, ".raw")
        frames, errors = [], []
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("日志需为 UTF-8；原文件已保存") from exc
        for i, line in enumerate(text.splitlines()):
            if not line.strip() or line.lstrip().startswith(";"):
                continue
            try:
                frames.append(parse_can_line(line))
            except (ValueError, TypeError) as exc:
                if len(errors) < 100:
                    errors.append({"line": i+1, "error": str(exc)[:500]})
        if len(frames) > 500000:
            raise ValueError("首版单次分析最多 50 万帧，请按采集会话分段；原文件已保存")
        session = self.store.create("session", {"project": project, "name": name, "artifact": artifact,
            "count": len(frames), "errors": errors, "demo": demo,
            "quality": {"error_frames":sum(f.error for f in frames), "reported_drops":sum(f.dropped or 0 for f in frames),
                        "drop_count_known":all(f.dropped is not None for f in frames)},
            "status": "ready" if frames and not errors else "needs_review",
            "scope": self.store.get(project, "project")["scope"],
            "content_hash": digest([f.model_dump() for f in frames])}, project)
        self.store.index_frames(session["id"], frames)
        return session

    def import_reference(self, project, session_id, name, data, meta, est=False):
        self.belongs(session_id, "session", project)
        meta = ReferenceMeta.model_validate(meta).model_dump()
        artifact = self.store.artifact(data, ".reference.raw")
        rows, parsed = [], []
        for line in data.decode("utf-8-sig").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if est:
                if meta["source"] != "est_serial":
                    raise ValueError("EST 参考必须标记 est_serial 来源")
                value = parse_est(item["raw"], float(item["timestamp"]))
                parsed.append(value)
                rows.extend({k: v for k,v in field.items() if k in Reference.model_fields} for field in value["fields"])
            else:
                rows.append(Reference.model_validate(item).model_dump())
        if len(rows) > 500000:
            raise ValueError("参考样本超过首版上限")
        return self.store.create("reference", {"project": project, "session_id": session_id, "name": name,
            "artifact": artifact, "meta": meta, "rows": rows, "changes": est_changes(parsed),
            "fields":sorted({r["field"] for r in rows}), "count":len(rows),
            "unknown_lines": [x for x in parsed if x["quality"] == "unknown"][:100]}, project)

    @serialized
    def candidate(self, project, definition, supersedes=None):
        candidate = Candidate.model_validate(definition)
        self.store.get(project, "project")
        for session_id in candidate.source_sessions:
            self.belongs(session_id, "session", project)
        previous = None
        if supersedes:
            previous = self.belongs(supersedes, "candidate", project)
            # Provenance is append-only across revisions, including superseded training data.
            candidate.origins = list(dict.fromkeys(previous["definition"]["origins"] + candidate.origins))
            candidate.source_sessions = list(dict.fromkeys(previous["definition"]["source_sessions"] + candidate.source_sessions))
            if previous.get("validation_id"):
                report=self.belongs(previous["validation_id"],"validation",project)
                candidate.source_sessions=list(dict.fromkeys(candidate.source_sessions+[report["session_id"]]))
                reference=self.belongs(report["reference_id"],"reference",project)
                candidate.origins=list(dict.fromkeys(candidate.origins+[reference["meta"]["origin"]]))
            if previous["state"] != "published":
                self.store.update(supersedes, {"state": "invalidated"})
        return self.store.create("candidate", {"project": project, "definition": candidate.model_dump(),
            "definition_hash": digest(candidate.model_dump()), "state": "candidate", "supersedes": supersedes,
            "scope": self.store.get(project, "project")["scope"]}, project)

    @serialized
    def plan_validation(self, project, payload):
        plan = ValidationPlan.model_validate(payload).model_dump()
        record = self.belongs(plan["candidate_id"], "candidate", project)
        candidate = Candidate.model_validate(record["definition"])
        if any(any(word in str(value).lower() for word in ("待确认", "unknown", "待定")) for value in record["scope"].values()):
            raise ValueError("先明确车型版本与总线接入点，未知适用范围不能正式验证")
        if record["state"] != "reference_matched":
            raise ValueError("先完成参考匹配，再冻结独立验证计划")
        if candidate.unresolved:
            raise ValueError("候选仍有未解决问题；修订规则和证据后重新匹配")
        session = self.belongs(plan["session_id"], "session", project)
        reference = self.belongs(plan["reference_id"], "reference", project)
        if session["scope"] != record["scope"]:
            raise ValueError("验证会话的车型/版本/接入点与候选适用范围不一致")
        if reference["session_id"] != session["id"]:
            raise ValueError("参考必须来自验证会话")
        if session["demo"] or session["status"] != "ready":
            raise ValueError("演示或存在解析错误的会话不能用于发布验证")
        if session.get("quality",{}).get("error_frames") or session.get("quality",{}).get("reported_drops"):
            raise ValueError("验证会话存在已知总线错误或丢帧，请重新采集完整验证数据")
        if reference["meta"]["source"] not in ("observation", "instrument"):
            raise ValueError("独立验证需直接观察或独立仪器，EST 同源参考不能发布")
        if reference["meta"]["origin"] in candidate.origins:
            raise ValueError("参考与候选来源相同")
        if reference["meta"]["unit"] != candidate.unit:
            raise ValueError("参考与候选单位不一致")
        if reference["meta"]["accuracy"] > plan["max_error"]:
            raise ValueError("参考精度不足以验证所设误差门槛")
        if reference["meta"]["time_uncertainty"] > candidate.freshness:
            raise ValueError("参考时间不确定度超出有效时间")
        self.require_held_out(project,session)
        for source in candidate.source_sessions:
            old = self.belongs(source, "session", project)
            if session.get("capture_id") and old.get("capture_id")==session["capture_id"]:
                raise ValueError("同一采集任务的分块不能作为独立验证会话")
            if old["content_hash"] == session["content_hash"] or old["artifact"]["sha256"] == session["artifact"]["sha256"]:
                raise ValueError("验证会话与发现/标定数据重复")
        if not candidate.source_sessions:
            raise ValueError("缺少发现/标定会话来源")
        if set(plan["coverage"]) != {"positive", "negative", "invalid", "timeout"}:
            raise ValueError("需规划正例、反例、失效与超时四类证据")
        result = self.store.create("validation_plan", {"project": project, **plan,
            "definition_hash": record["definition_hash"], "reference_hash": reference["artifact"]["sha256"],
            "scope": record["scope"]}, project)
        self.store.update(record["id"], {"state": "pending_validation"})
        return result

    def require_held_out(self,project,session):
        for job in self.store.list("job",project):
            if job["kind"] not in ("fit","search"):
                continue
            used=self.belongs(job["inputs"]["session_id"],"session",project)
            if used["content_hash"]==session["content_hash"] or (session.get("capture_id") and used.get("capture_id")==session["capture_id"]):
                raise ValueError("该会话已用于候选搜索/拟合，不能再作为独立验证集；请采集新会话")

    def effective_references(self, project, session, field, rows):
        exclusions = [a for a in self.store.list("annotation",project) if a["session_id"]==session and
                      a["field"]==field and a.get("excluded") and not a.get("superseded_by")]
        return [{**r,"quality":"excluded"} if any(a["start"]<=r["timestamp"]<=a["end"] for a in exclusions) else r
                for r in rows if r["field"]==field]

    @serialized
    def run_validation(self, project, plan_id):
        plan = self.belongs(plan_id, "validation_plan", project)
        record = self.belongs(plan["candidate_id"], "candidate", project)
        if record["state"] != "pending_validation" or record["definition_hash"] != plan["definition_hash"]:
            raise ValueError("验证计划已失效或已执行")
        session = self.belongs(plan["session_id"], "session", project)
        self.require_held_out(project,session)
        reference = self.belongs(plan["reference_id"], "reference", project)
        self.store.read_artifact(session["artifact"])
        self.store.read_artifact(reference["artifact"])
        candidate = Candidate.model_validate(record["definition"])
        frames = self.store.frames(session["id"])
        refs = self.effective_references(project,session["id"],plan["field"],reference["rows"])
        result = analysis.validate(frames, refs, candidate, reference["meta"], plan)
        annotations = [a for a in self.store.list("annotation", project) if a["session_id"] == session["id"]
                       and a["field"] == plan["field"] and not a.get("excluded") and not a.get("superseded_by")]
        covered = set()
        for annotation in annotations:
            inside = [r for r in refs if annotation["start"] <= r["timestamp"] <= annotation["end"]]
            if annotation["stage"] in ("positive", "negative") and any(r["quality"] == "valid" for r in inside):
                covered.add(annotation["stage"])
            elif annotation["stage"] == "invalid" and result["invalid_passed"] and any(r.get("expected_valid") is False for r in inside):
                covered.add("invalid")
            elif annotation["stage"] == "timeout":
                t = annotation["end"] * reference["meta"]["clock_scale"] + reference["meta"]["clock_offset"] - candidate.lag
                samples = analysis.series(frames, candidate)
                valid_before = [(ts,v) for ts,v in samples if ts <= t and v is not None]
                if inside and valid_before and t-valid_before[-1][0] > candidate.freshness and all(r["quality"] != "valid" and r.get("expected_valid") is False for r in inside):
                    covered.add("timeout")
        passed = result["passed_numeric"] and covered == set(plan["coverage"])
        report = self.store.create("validation", {"project": project, "plan_id": plan_id,
            "candidate_id": record["id"], "definition_hash": record["definition_hash"],
            "session_id": session["id"], "reference_id": reference["id"], "result": result,
            "coverage": sorted(covered), "passed": passed, "scope": plan["scope"],
            "annotations": annotations}, project)
        self.store.update(record["id"], {"state": "validated" if passed else "rejected", "validation_id": report["id"]})
        return report

    @serialized
    def publish(self, project, candidate_ids, experimental):
        if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("请选择不重复的候选")
        records = [self.belongs(i, "candidate", project) for i in candidate_ids]
        frames, reports = [], []
        loaded_sessions=set()
        scope = self.store.get(project, "project")["scope"]
        for record in records:
            if record["state"] in ("invalidated", "rejected"):
                raise ValueError("失效/拒绝的候选不可导出，请先修订")
            if not experimental:
                if record["state"] not in ("validated", "published") or record["scope"] != scope:
                    raise ValueError("正式导出仅接受当前适用范围内独立验证通过的候选")
                report = self.belongs(record["validation_id"], "validation", project)
                if not report["passed"] or report["definition_hash"] != record["definition_hash"]:
                    raise ValueError("验证证据无效")
                reports.append(report)
                source_ids = [report["session_id"]]
                reference = self.belongs(report["reference_id"], "reference", project)
                self.store.read_artifact(reference["artifact"])
            else:
                source_ids = record["definition"]["source_sessions"]
            for source in source_ids:
                session = self.belongs(source, "session", project)
                self.store.read_artifact(session["artifact"])
                if source not in loaded_sessions:
                    frames.extend(self.store.frames(source));loaded_sessions.add(source)
        candidates = [Candidate.model_validate(r["definition"]) for r in records]
        text, checks = export_dbc(candidates, frames)
        manifest = {"schema": 1, "experimental": experimental, "scope": scope, "candidates": records,
                    "validation": reports, "roundtrip_checks": checks,
                    "unknowns": [u for c in candidates for u in c.unresolved],
                    "warning": "DBC 不表达所有失效/超时/来源条件；必须连同本清单使用。非控制系统安全认证。"}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("EXPERIMENTAL.dbc" if experimental else "validated.dbc", text)
            archive.writestr("manifest.json", canonical(manifest))
            archive.writestr("validation.json", canonical(reports))
        artifact = self.store.artifact(stream.getvalue(), ".zip")
        publication = self.store.create("publication", {"project": project, "experimental": experimental,
            "artifact": artifact, "manifest": manifest}, project)
        if not experimental:
            for record in records:
                self.store.update(record["id"], {"state": "published"})
        return publication
