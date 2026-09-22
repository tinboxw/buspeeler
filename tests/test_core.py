import io
import json
import math
import zipfile

import numpy as np
import pytest
from fastapi.testclient import TestClient

from buspeeler.analysis import fit, statistics
from buspeeler.app import create_app
from buspeeler.dbc import export_dbc
from buspeeler.models import Candidate, Frame, Layout, decode, extract
from buspeeler.protocols import EST_FIELDS, SerialLines, parse_est, parse_can_line, isotp


def test_bits_endian_sign_and_fd():
    assert extract(bytes.fromhex("1234"),Layout(start=7,length=16,endian="big_endian"))==0x1234
    assert extract(bytes.fromhex("1234"),Layout(start=0,length=16))==0x3412
    assert extract(b"\xff\xff",Layout(start=4,length=12,signed=True))==-1
    assert extract(bytes.fromhex("1234"),Layout(start=3,length=8,endian="big_endian"))==0x23
    frame=parse_can_line("(1.5) can1 00000123##1"+"AA"*64)
    assert frame.fd and frame.extended and frame.brs and len(frame.data)==128
    assert parse_can_line("(1) can0 123#R8").remote
    with pytest.raises(ValueError): Frame(timestamp=0,can_id=0x800)
    with pytest.raises(ValueError): Frame(timestamp=0,can_id=1,fd=True,data="00"*9)
    with pytest.raises(ValueError): Frame(timestamp=0,can_id=1,data="AA",dlc=2)


def test_est_chunks_unknowns_percent_and_direction():
    values=["#"]*39; values[6]="R25"; values[14]="1"
    line="$OBD-RT,"+",".join(values)
    parser=SerialLines()
    assert parser.feed(line[:20].encode())==[]
    lines=parser.feed((line[20:]+"\r\n"+line+"\n").encode())
    assert len(lines)==2
    parsed=parse_est(lines[0],1)
    steering=parsed["fields"][6]
    assert steering["value"]==25 and steering["unit"]=="%" and steering["direction"]=="R"
    assert parsed["fields"][0]["quality"]=="unsupported"
    assert parse_est(line+",1",1)["quality"]=="unknown"
    assert parse_est(line.replace("R25","L139"),1)["fields"][6]["value"]==139
    limited=SerialLines(10)
    assert limited.feed(b"a"*20+b"\nOK\n")==["[oversized line]","OK"]


def test_integer_firmware_behaviour():
    candidate=Candidate(name="speed",can_id=0x320,message_length=8,layout=Layout(start=40,length=16),origins=["firmware"],postprocess="va3_speed")
    frame=Frame(timestamp=0,can_id=0x320,data="0000000000CF0700")
    assert decode(frame,candidate)==9
    with pytest.raises(ValueError,match="后处理"):export_dbc([candidate],[frame])
    candidate=Candidate(name="turn",can_id=0xc2,message_length=8,layout=Layout(start=0,length=16),origins=["firmware"],postprocess="va3_steering")
    assert decode(Frame(timestamp=0,can_id=0xc2,data="800B000000000000"),candidate)==25


def test_indicator_hold_is_not_a_stateless_dbc_signal():
    from buspeeler.analysis import series
    candidate=Candidate(name="left",can_id=0x392,message_length=8,layout=Layout(start=11,length=1),
        origins=["firmware"],postprocess="va3_indicator_hold",freshness=.5)
    frames=[Frame(timestamp=i*.1,can_id=0x392,data="0008000000000000" if i==0 else "0000000000000000") for i in range(7)]
    assert [v for _,v in series(frames,candidate)]==[1,1,1,1,1,0,0]
    assert series([Frame(timestamp=0,can_id=0x392,data="0000000000000000")],candidate)==[(0,None)]
    with pytest.raises(ValueError,match="后处理"): export_dbc([candidate],frames)


def test_fit_lag_factor_offset_and_constant_rejection():
    samples=[(i/10,math.sin(i/13)*200+i) for i in range(300)]
    references=[{"timestamp":t+0.2,"value":v*0.5-40,"quality":"valid"} for t,v in samples]
    result=fit(samples,references,{},0.15,0,0.4,0.1)["best"]
    assert result["factor"]==pytest.approx(0.5,abs=0.01)
    assert result["offset"]==pytest.approx(-40,abs=0.2)
    assert result["lag"]==pytest.approx(0.2)
    with pytest.raises(ValueError):fit([(i,0) for i in range(20)],references,{},1)


def test_passive_isotp_sequence_and_timeout():
    def f(t,data):return Frame(timestamp=t,can_id=0x123,data=data)
    output=isotp([f(0,"100A010203040506"),f(.1,"210708090A000000")])
    assert output[0]["status"]=="complete" and output[0]["data"]=="0102030405060708090a"
    assert isotp([f(0,"100A010203040506"),f(.1,"220708090A000000")])[0]["status"]=="sequence_error"
    assert isotp([f(0,"100A010203040506"),f(2,"210708090A000000")])[0]["status"]=="timeout"


@pytest.fixture
def client(tmp_path):
    app=create_app(tmp_path,"test-token",expected_host="testserver")
    with TestClient(app,headers={"Authorization":"Bearer test-token"}) as client:
        yield client


def project(client,version="v1"):
    r=client.post("/api/projects",json={"name":"测试项目","scope":{"vehicle":"VA3","year":2023,"transmission":"MT","bus":"body-can/testpoint-A","version":version}})
    assert r.status_code==200,r.text
    return r.json()["id"]


def import_frames(client,p,start,name="CAN"):
    frames=[{"timestamp":start+i,"can_id":0x390,"data":f"0000{(i//5)%2:02X}0000000000"} for i in range(30)]
    frames.append({"timestamp":start+30,"can_id":0x390,"data":"0000FF0000000000"})
    content="\n".join(json.dumps(f) for f in frames)
    r=client.post(f"/api/projects/{p}/can",files={"file":(name+".jsonl",content)})
    assert r.status_code==200,r.text
    return r.json(),content


def import_refs(client,p,s,start,source,origin):
    rows=[{"timestamp":start+i,"field":"door","value":(i//5)%2} for i in range(30)]
    rows.extend([{"timestamp":start+30,"field":"door","value":None,"quality":"unknown","expected_valid":False},
                 {"timestamp":start+32,"field":"door","value":None,"quality":"unknown","expected_valid":False}])
    meta={"source":source,"origin":origin,"unit":"","accuracy":0,"time_uncertainty":0,
          "description":"独立观察与设备时钟同步，分别记录开关与失效事件"}
    r=client.post(f"/api/projects/{p}/reference",data={"session_id":s,"meta":json.dumps(meta),"est":"false"},
                  files={"file":("refs.jsonl","\n".join(json.dumps(x) for x in rows))})
    assert r.status_code==200,r.text
    return r.json()


def test_api_security_scope_and_demo_gate(client):
    assert client.get("/api/projects",headers={"Authorization":""}).status_code==401
    assert client.get("/api/projects",headers={"Host":"evil.local"}).status_code==403
    assert client.post("/api/projects",headers={"Origin":"https://evil.local"},json={}).status_code==403
    p,q=project(client),project(client)
    demo=client.post(f"/api/projects/{p}/demo",json={}).json()
    s,c=demo["session"]["id"],demo["candidate"]["id"]
    assert client.get(f"/api/projects/{q}/sessions/{s}/frames").status_code==400
    assert client.post(f"/api/projects/{p}/exports",json={"candidates":[c],"experimental":False}).status_code==400
    publication=client.post(f"/api/projects/{p}/exports",json={"candidates":[c],"experimental":True})
    assert publication.status_code==200,publication.text
    data=client.get(f"/api/projects/{p}/exports/{publication.json()['id']}").content
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "EXPERIMENTAL.dbc" in archive.namelist()
    assert client.get(f"/api/projects/{p}/sessions/{s}/plot?message=can0:390:0:0:8&candidate={c}").status_code==200


def test_independent_validation_and_immutable_publication(client):
    p=project(client); base=f"/api/projects/{p}"
    training,_=import_frames(client,p,0)
    calibration=import_refs(client,p,training["id"],0,"observation","calibration-observer")
    definition={"name":"Door","can_id":0x390,"message_length":8,"layout":{"start":16,"length":8},
        "invalid_raw":[255],"origins":["calibration-observer"],"source_sessions":[training["id"]],
        "evidence":"车门操作、独立观察；取值 0/1，其余值须拒绝","freshness":1.1}
    record=client.post(base+"/candidates",json={"definition":definition}).json()
    match=client.post(base+f"/candidates/{record['id']}/match",json={"session_id":training["id"],"reference_id":calibration["id"],"field":"door","max_error":0})
    assert match.status_code==200,match.text
    validation,_=import_frames(client,p,100)
    reference=import_refs(client,p,validation["id"],100,"instrument","independent-camera")
    plan={"candidate_id":record["id"],"session_id":validation["id"],"reference_id":reference["id"],"field":"door",
          "max_error":0,"min_samples":20,"range_min":0,"range_max":1,
          "coverage":["positive","negative","invalid","timeout"],"observation":"已预先约定四类独立观察操作，并锁定参考精度"}
    common=import_refs(client,p,validation["id"],100,"instrument","calibration-observer")
    assert client.post(base+"/validation-plans",json={**plan,"reference_id":common["id"]}).status_code==400
    dup_ref=import_refs(client,p,training["id"],0,"instrument","independent-camera")
    assert client.post(base+"/validation-plans",json={**plan,"session_id":training["id"],"reference_id":dup_ref["id"]}).status_code==400
    for stage,start,end in [("positive",100,110),("negative",111,129),("invalid",130,130),("timeout",132,132)]:
        r=client.post(base+"/annotations",json={"session_id":validation["id"],"field":"door","start":start,"end":end,"stage":stage,"note":"直接观察事件与独立参考记录一致"})
        assert r.status_code==200
    frozen=client.post(base+"/validation-plans",json=plan)
    assert frozen.status_code==200,frozen.text
    report=client.post(base+f"/validation-plans/{frozen.json()['id']}/run",json={})
    assert report.status_code==200,report.text
    assert report.json()["passed"],report.text
    publication=client.post(base+"/exports",json={"candidates":[record["id"]],"experimental":False})
    assert publication.status_code==200,publication.text
    new=client.post(base+"/candidates",json={"definition":{**definition,"factor":2},"supersedes":record["id"]})
    assert new.status_code==200
    assert new.json()["state"]=="candidate"
    assert client.post(base+"/exports",json={"candidates":[new.json()["id"]],"experimental":False}).status_code==400
    downloaded=client.get(base+f"/exports/{publication.json()['id']}")
    assert downloaded.status_code==200


def test_dbc_big_endian_signed_mux_roundtrip():
    candidate=Candidate(name="Value",can_id=0x321,message_length=8,layout=Layout(start=15,length=12,endian="big_endian",signed=True),
        factor=.25,offset=-40,unit="degC",origins=["test"],mux={"selector":{"start":0,"length":4},"value":2})
    frames=[Frame(timestamp=0,can_id=0x321,data="02FFF00000000000")]
    assert decode(frames[0],candidate)==-40.25
    text,count=export_dbc([candidate],frames)
    assert count==1 and "m2" in text


def test_recovery_and_raw_integrity(tmp_path):
    from buspeeler.store import Store
    store=Store(tmp_path)
    job=store.create("job",{"status":"running"})
    store.recover()
    assert store.get(job["id"])["status"]=="interrupted"
    item=store.artifact(b"original",".raw")
    (tmp_path/"artifacts"/item["file"]).write_bytes(b"changed")
    with pytest.raises(ValueError,match="已变更"):store.read_artifact(item)
    store.db.close()


def test_unknown_reference_is_not_signal_invalid_evidence():
    from buspeeler.analysis import validate
    candidate=Candidate(name="Value",can_id=1,message_length=1,layout=Layout(start=0,length=8),origins=["test"])
    refs=[{"timestamp":2,"field":"x","value":None,"quality":"unknown"}]
    result=validate([Frame(timestamp=0,can_id=1,data="01")],refs,candidate,{},
                    {"min_samples":3,"max_error":0,"range_min":0,"range_max":1})
    assert result["invalid_checked"]==0 and not result["invalid_passed"]


def test_annotation_exclusion_scope_revision_and_source_append_only(client):
    p=project(client);base=f"/api/projects/{p}"
    session,_=import_frames(client,p,0)
    reference=import_refs(client,p,session["id"],0,"instrument","source-a")
    definition={"name":"Door","can_id":912,"message_length":8,"layout":{"start":16,"length":1},
                "origins":["source-a"],"source_sessions":[session["id"]]}
    candidate=client.post(base+"/candidates",json={"definition":definition}).json()
    revised=client.post(base+"/candidates",json={"definition":{**definition,"origins":["source-b"],"source_sessions":[]},"supersedes":candidate["id"]}).json()
    assert revised["definition"]["origins"]==["source-a","source-b"]
    assert revised["definition"]["source_sessions"]==[session["id"]]
    annotation=client.post(base+"/annotations",json={"session_id":session["id"],"field":"door","start":0,"end":2,"stage":"baseline","note":"此区间参考测量无效","excluded":True}).json()
    refs=client.app.state.service.effective_references(p,session["id"],"door",reference["rows"])
    assert [r["quality"] for r in refs[:3]]==["excluded"]*3
    updated=client.put(base,json={"name":"新范围","scope":{"vehicle":"VA3","year":2023,"transmission":"MT","bus":"other","version":"v2"}})
    assert updated.status_code==200
    assert client.app.state.store.get(revised["id"])["state"]=="invalidated"


def test_steering_direction_is_not_lost_in_matching():
    from buspeeler.analysis import direction_checks
    candidate=Candidate(name="steering",can_id=0xc2,message_length=8,layout=Layout(start=0,length=16),
                        origins=["test"],postprocess="va3_steering")
    frame=Frame(timestamp=0,can_id=0xc2,data="808B000000000000")
    refs=[{"timestamp":0,"quality":"valid","value":25,"direction":"L"}]
    assert not direction_checks([frame],refs,candidate,{})["passed"]
    refs[0]["direction"]="R"
    assert direction_checks([frame],refs,candidate,{})["passed"]


def test_search_data_cannot_be_relabelled_as_held_out(client):
    p=project(client)
    session,content=import_frames(client,p,0)
    store=client.app.state.store
    store.create("job",{"project":p,"kind":"search","status":"completed","inputs":{"session_id":session["id"]}},p)
    duplicate=client.post(f"/api/projects/{p}/can",files={"file":("renamed.jsonl",content)}).json()
    with pytest.raises(ValueError,match="已用于候选搜索"):
        client.app.state.service.require_held_out(p,duplicate)
