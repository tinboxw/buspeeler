import hmac
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .analysis import align, series, statistics, identity, direction_checks
from .capture import CaptureManager
from .dbc import import_dbc
from .jobs import Jobs
from .models import Model, Project, Candidate, ReferenceMeta
from .service import Service
from .store import Store, digest

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


class CandidateRequest(Model):
    definition: Candidate
    supersedes: str | None = None


class JobRequest(Model):
    kind: Literal["statistics", "search", "fit", "isotp"]
    session_id: str
    candidate_id: str | None = None
    reference_id: str | None = None
    field: str = ""
    message: str = ""
    max_length: int = Field(default=16, ge=1, le=32)
    start: float = Field(default=0, ge=0)
    end: float = Field(default=1e20, ge=0)


class Annotation(Model):
    session_id: str
    field: str = Field(min_length=1, max_length=80)
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    stage: Literal["baseline", "positive", "negative", "invalid", "timeout", "repeat"]
    note: str = Field(min_length=1, max_length=4000)
    excluded: bool = False
    supersedes: str | None = None


class Match(Model):
    session_id: str
    reference_id: str
    field: str
    max_error: float = Field(ge=0)


class Export(Model):
    candidates: list[str] = Field(min_length=1, max_length=100)
    experimental: bool = True


class CaptureConfig(Model):
    backend: Literal["socketcan", "zlg", "chuangxin"]
    profile: str = ""
    interface: str = "can0"
    index: int = Field(default=0, ge=0, le=255)
    channel: int = Field(default=0, ge=-1, le=1)
    bitrate: int = Field(default=500000, ge=1000, le=1000000)
    serial_port: str = ""
    baudrate: Literal[9600, 38400, 115200] = 115200
    bytesize: Literal[7, 8] = 8
    parity: Literal["N", "E", "O"] = "N"
    stopbits: Literal[1, 2] = 1
    est_origin: str = "est-unknown"
    configuration_confirmed: bool = False


def create_app(root=None, token=None, expected_host="127.0.0.1:8765"):
    root = Path(root or os.getenv("BUSPEELER_DATA", Path.home()/".buspeeler"))
    store = Store(root)
    store.recover()
    service, jobs = Service(store), Jobs(store)
    collector = Path(os.getenv("BUSPEELER_COLLECTOR", str(BASE/"collector"/("buspeeler-collector.exe" if os.name=="nt" else "buspeeler-collector"))))
    capture = CaptureManager(store, service, collector)
    secret = token or secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        capture.close()
        jobs.close()
        store.db.close()

    app = FastAPI(title="Buspeeler", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.service, app.state.token = store, service, secret

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        if request.headers.get("host") != expected_host:
            return JSONResponse({"detail": "仅接受已绑定的本机地址"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != f"http://{expected_host}":
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + secret):
                return JSONResponse({"detail": "请从应用启动入口打开工作台"}, status_code=401)
        try:
            content_length=int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail":"请求长度无效"},status_code=400)
        if content_length > 70*1024*1024:
            return JSONResponse({"detail": "单次上传上限 64 MiB"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    async def upload(file):
        data = await file.read(64*1024*1024+1)
        await file.close()
        if len(data)>64*1024*1024: raise ValueError("单文件上限 64 MiB，请分段导入")
        return data

    @app.get("/api/health")
    def health(): return {"status": "ready", "version": "0.1.0"}

    @app.get("/api/projects")
    def projects(): return store.list("project")

    @app.post("/api/projects")
    def project(payload: Project): return store.create("project", payload.model_dump())

    @app.put("/api/projects/{project}")
    def update_project(project: str,payload: Project):
        old=store.get(project,"project")
        if old["scope"]!=payload.scope.model_dump():
            for record in store.list("candidate",project):
                if record["state"]!="published": store.update(record["id"],{"state":"invalidated"})
        return store.update(project,payload.model_dump())

    @app.get("/api/projects/{project}/workspace")
    def workspace(project: str):
        store.get(project,"project")
        result = {}
        for kind in ("session", "reference", "candidate", "annotation", "job", "validation_plan", "validation", "publication", "capture"):
            result[kind] = store.list(kind,project,summary=True)
        return result

    @app.post("/api/projects/{project}/can")
    async def import_can(project: str, file: UploadFile=File(...)):
        return service.import_can(project, file.filename or "CAN", await upload(file))

    @app.post("/api/projects/{project}/reference")
    async def import_reference(project: str, session_id: str=Form(...), meta: str=Form(...), est: bool=Form(False), file: UploadFile=File(...)):
        return service.import_reference(project,session_id,file.filename or "参考", await upload(file),json.loads(meta),est)

    @app.get("/api/projects/{project}/references/{ident}")
    def reference(project: str, ident: str): return service.belongs(ident,"reference",project)

    @app.get("/api/projects/{project}/sessions/{ident}/frames")
    def frames(project: str, ident: str, message: str|None=None, start: float=0, end: float=1e20, offset: int=0, limit: int=1000):
        service.belongs(ident,"session",project)
        if start<0 or end<start or offset<0 or not 1<=limit<=5000: raise ValueError("查询范围无效")
        return [f.model_dump() for f in store.frames(ident,message,start,end,limit,offset)]

    @app.get("/api/projects/{project}/sessions/{ident}/messages")
    def messages(project: str, ident: str):
        service.belongs(ident,"session",project)
        with store.lock:
            rows=store.db.execute("SELECT message,COUNT(*),MIN(time),MAX(time) FROM frames WHERE session=? GROUP BY message",(ident,)).fetchall()
        return [{"key":r[0],"count":r[1],"start":r[2],"end":r[3]} for r in rows]

    @app.get("/api/projects/{project}/sessions/{ident}/plot")
    def plot(project: str, ident: str, message: str, start: float=0, end: float=1e20,
             candidate: str|None=None, reference: str|None=None, field: str=""):
        service.belongs(ident,"session",project)
        if start<0 or end<start: raise ValueError("时间区间无效")
        selected=store.frames(ident,message,start,end)
        output=[]
        if candidate:
            definition=Candidate.model_validate(service.belongs(candidate,"candidate",project)["definition"])
            values=[]
            previous=None
            for t,v in series(selected,definition):
                if previous is not None and t-previous>definition.freshness:
                    values.append((previous+definition.freshness,None))
                values.append((t,v));previous=t
            output.append({"name":definition.name,"data":values})
        else:
            output.append({"name":"byte 0（原始值）","data":[(f.timestamp,int(f.data[:2],16) if f.data else None) for f in selected]})
        if reference:
            ref=service.belongs(reference,"reference",project)
            if ref["session_id"]!=ident: raise ValueError("参考会话不一致")
            meta=ref["meta"]
            reference_rows=service.effective_references(project,ident,field,ref["rows"])
            output.append({"name":field+" · 参考","data":[(r["timestamp"]*meta["clock_scale"]+meta["clock_offset"],r["value"] if r["quality"]=="valid" else None)
                for r in reference_rows if start<=r["timestamp"]*meta["clock_scale"]+meta["clock_offset"]<=end]})
        # Keep extrema and invalid gaps per bucket instead of drawing every frame.
        for item in output:
            rows=sorted(item["data"],key=lambda x:x[0])
            if len(rows)>1500:
                size=(len(rows)+499)//500
                reduced=[]
                for i in range(0,len(rows),size):
                    bucket=rows[i:i+size]
                    valid=[x for x in bucket if x[1] is not None]
                    chosen=[bucket[0],bucket[-1]]
                    if valid: chosen.extend([min(valid,key=lambda x:x[1]),max(valid,key=lambda x:x[1])])
                    invalid=next((x for x in bucket if x[1] is None),None)
                    if invalid: chosen.append(invalid)
                    reduced.extend(sorted(set(chosen),key=lambda x:x[0]))
                item["data"]=reduced
        return output

    @app.post("/api/projects/{project}/candidates")
    def add_candidate(project: str, payload: CandidateRequest):
        return service.candidate(project,payload.definition.model_dump(),payload.supersedes)

    @app.post("/api/projects/{project}/dbc")
    async def dbc(project: str, channel: str=Form("can0"), file: UploadFile=File(...)):
        data=await upload(file)
        artifact=store.artifact(data,".dbc")
        definitions,unsupported=import_dbc(data.decode("utf-8-sig"),"dbc:"+artifact["sha256"],channel)
        return {"candidates":[service.candidate(project,d) for d in definitions], "unsupported":unsupported}

    @app.post("/api/projects/{project}/firmware-seeds")
    def seeds(project: str):
        source=json.loads((BASE/"profiles"/"va3-mt-2019.json").read_text(encoding="utf-8"))
        return [service.candidate(project,d) for d in source["candidates"]]

    @app.get("/api/firmware")
    def firmware():
        path=BASE/"profiles"/"firmware-catalog.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"files":[],"status":"未生成目录"}

    @app.post("/api/projects/{project}/annotations")
    def annotate(project: str, payload: Annotation):
        service.belongs(payload.session_id,"session",project)
        if payload.end<payload.start: raise ValueError("结束时间不能早于开始时间")
        if payload.supersedes:
            old=service.belongs(payload.supersedes,"annotation",project)
            if old["session_id"]!=payload.session_id or old.get("superseded_by"):
                raise ValueError("标注来源不匹配或已修订")
        result=store.create("annotation",{"project":project,**payload.model_dump()},project)
        if payload.supersedes: store.update(payload.supersedes,{"superseded_by":result["id"]})
        return result

    @app.post("/api/projects/{project}/jobs")
    def job(project: str, payload: JobRequest):
        session=service.belongs(payload.session_id,"session",project)
        store.read_artifact(session["artifact"])
        if payload.end<payload.start:raise ValueError("分析时间窗口无效")
        data={"frames":[f.model_dump() for f in store.frames(payload.session_id, payload.message or None,payload.start,payload.end)],
              "message":payload.message,"max_length":payload.max_length}
        if payload.reference_id:
            ref=service.belongs(payload.reference_id,"reference",project)
            if ref["session_id"]!=payload.session_id: raise ValueError("参考不属于分析会话")
            store.read_artifact(ref["artifact"])
            references=service.effective_references(project,payload.session_id,payload.field,ref["rows"])
            data.update(references=[r for r in references if payload.start<=r["timestamp"]*ref["meta"]["clock_scale"]+ref["meta"]["clock_offset"]<=payload.end],meta=ref["meta"])
        if payload.candidate_id:
            record=service.belongs(payload.candidate_id,"candidate",project)
            data["candidate"]=record["definition"]
        if payload.kind in ("search","fit") and not payload.reference_id: raise ValueError("请选择参考数据")
        if payload.kind=="fit" and not payload.candidate_id: raise ValueError("请选择候选")
        if payload.kind in ("search","isotp") and not payload.message: raise ValueError("请选择一条报文")
        return jobs.submit(project,payload.kind,data,payload.model_dump())

    @app.post("/api/projects/{project}/jobs/{ident}/adopt")
    def adopt(project: str,ident: str,rank: int=0):
        job=service.belongs(ident,"job",project)
        if job["status"]!="completed" or job["kind"] not in ("fit","search"): raise ValueError("任务结果不可转为候选")
        inputs=job["inputs"]
        reference=service.belongs(inputs["reference_id"],"reference",project)
        previous=None
        if job["kind"]=="fit":
            previous=service.belongs(inputs["candidate_id"],"candidate",project)
            definition=dict(previous["definition"])
            best=job["result"]["best"]
            definition.update({key:best[key] for key in ("factor","offset","lag")})
        else:
            results=job["result"]["candidates"]
            if not 0<=rank<len(results): raise ValueError("结果序号无效")
            best=results[rank]
            channel,ident_hex,extended,fd,length=inputs["message"].split(":")
            definition={"name":"Signal_"+ident_hex,"channel":channel,"can_id":int(ident_hex,16),"extended":extended=="1","fd":fd=="1",
                        "message_length":int(length),"layout":best["layout"],"factor":best["factor"],"offset":best["offset"],
                        "unit":reference["meta"]["unit"],"origins":[],"source_sessions":[],"semantic":inputs["field"],
                        "unresolved":["位边界、符号、字节序和物理语义需独立确认"]}
        definition["origins"]=list(dict.fromkeys(definition["origins"]+[reference["meta"]["origin"]]))
        definition["source_sessions"]=list(dict.fromkeys(definition["source_sessions"]+[inputs["session_id"]]))
        definition["evidence"]=definition.get("evidence","")+f"\n分析任务 {job['id']}，结果 {rank}，拟合误差 {best['rmse']}"
        return service.candidate(project,definition,previous["id"] if previous else None)

    @app.get("/api/projects/{project}/sessions/{ident}/template-match")
    def template_match(project: str,ident: str):
        service.belongs(ident,"session",project)
        frames=store.frames(ident)
        groups={identity(f) for f in frames}
        matches=[]
        for record in store.list("candidate",project):
            c=Candidate.model_validate(record["definition"])
            key=f"{c.channel}:{c.can_id:X}:{int(c.extended)}:{int(c.fd)}:{c.message_length}"
            decoded=[v for _,v in series(frames,c) if v is not None] if key in groups else []
            matches.append({"candidate":c.name,"visible":key in groups,"valid_samples":len(decoded),
                            "range":[min(decoded),max(decoded)] if decoded else None,
                            "status":"结构相容，仅为候选" if decoded else "当前记录未覆盖"})
        return matches

    @app.post("/api/projects/{project}/candidates/{ident}/match")
    def match(project: str, ident: str, payload: Match):
        record=service.belongs(ident,"candidate",project)
        if record["state"] not in ("candidate", "reference_matched"): raise ValueError("当前状态不可重新匹配；请创建修订")
        candidate=Candidate.model_validate(record["definition"])
        if payload.session_id not in candidate.source_sessions: raise ValueError("请先修订候选并登记发现/标定会话")
        reference=service.belongs(payload.reference_id,"reference",project)
        if reference["session_id"]!=payload.session_id: raise ValueError("参考会话不匹配")
        session=service.belongs(payload.session_id,"session",project)
        store.read_artifact(session["artifact"]); store.read_artifact(reference["artifact"])
        x,y,_,missing=align(series(store.frames(payload.session_id),candidate),
            service.effective_references(project,payload.session_id,payload.field,reference["rows"]),candidate.lag,candidate.freshness,reference["meta"])
        if len(x)<3 or missing or float(max(abs(x-y)))>payload.max_error: raise ValueError("参考匹配未通过；检查误差、有效样本及时间对齐")
        directions=direction_checks(store.frames(payload.session_id),reference["rows"],candidate,reference["meta"])
        if directions and not directions["passed"]: raise ValueError("转向 R/L 编码匹配未通过或参考方向缺失")
        # Store common origin even when the original candidate was manually entered.
        if reference["meta"]["origin"] not in candidate.origins:
            raise ValueError("请在候选来源中登记此参考来源，再创建修订并匹配")
        return store.update(ident,{"state":"reference_matched","match":{"reference_id":payload.reference_id,"samples":len(x),"max_error":float(max(abs(x-y)))}})

    @app.post("/api/projects/{project}/validation-plans")
    async def validation_plan(project: str, request: Request): return service.plan_validation(project,await request.json())

    @app.post("/api/projects/{project}/validation-plans/{ident}/run")
    def validation(project: str, ident: str): return service.run_validation(project,ident)

    @app.post("/api/projects/{project}/exports")
    def export(project: str, payload: Export): return service.publish(project,payload.candidates,payload.experimental)

    @app.get("/api/projects/{project}/exports/{ident}")
    def download(project: str, ident: str):
        item=service.belongs(ident,"publication",project)
        return Response(store.read_artifact(item["artifact"]),media_type="application/zip",
                        headers={"Content-Disposition":'attachment; filename="buspeeler-'+ident+'.zip"'})

    @app.get("/api/devices")
    def devices(): return capture.devices()

    @app.post("/api/projects/{project}/captures")
    def start_capture(project: str, payload: CaptureConfig):
        if not payload.configuration_confirmed: raise ValueError("请核对接线、电平、串口参数与监听配置")
        return capture.start(project,payload.model_dump())

    @app.post("/api/projects/{project}/captures/{ident}/stop")
    def stop_capture(project: str,ident: str):
        service.belongs(ident,"capture",project)
        return capture.stop(ident)

    @app.post("/api/projects/{project}/demo")
    def demo(project: str):
        lines,refs=[],[]
        for i in range(200):
            value=(i//20)%2
            lines.append(json.dumps({"timestamp":i*0.1,"channel":"can0","can_id":0x390,"data":f"0000{value:02X}0000000000"}))
            refs.append(json.dumps({"timestamp":i*0.1,"field":"driver_door","value":value}))
        session=service.import_can(project,"合成演示 · 不可用于正式验证", "\n".join(lines).encode(),True)
        ref=service.import_reference(project,session["id"],"合成车门参考","\n".join(refs).encode(),
            {"source":"demo","origin":"demo-generator","unit":"","accuracy":0,"time_uncertainty":0,"description":"合成数据，仅用于操作演示"})
        candidate=service.candidate(project,{"name":"DriverDoor","can_id":0x390,"message_length":8,
            "layout":{"start":16,"length":1},"origins":["demo-generator"],"source_sessions":[session["id"]],
            "semantic":"driver_door","evidence":"合成演示；不属于车辆证据"})
        return {"session":session,"reference":ref,"candidate":candidate}

    static=BASE/"frontend"/"dist"
    if static.exists():
        app.mount("/",StaticFiles(directory=static,html=True),name="web")
    else:
        @app.get("/")
        def missing_frontend(): return Response("页面尚未构建，请执行开发文档中的构建步骤。",media_type="text/plain")
    return app
