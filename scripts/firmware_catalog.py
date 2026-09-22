"""Read-only source indexing. Produces metadata; never executes firmware."""
import hashlib
import json
import re
import struct
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"docs"/"EST580"/"固件版本"


def main():
    entries=[]
    for path in sorted(SOURCE.glob("*.bin")):
        data=path.read_bytes()
        strings=re.findall(rb"\$OBD-RT[^\x00\r\n]*",data)
        formats=sorted({s.count(b",") for s in strings if b"%" in s})
        stack,reset=struct.unpack_from("<II",data)
        entries.append({"name":path.name,"bytes":len(data),"sha256":hashlib.sha256(data).hexdigest(),
            "initial_sp":f"0x{stack:08X}","reset_vector":f"0x{reset:08X}","rt_formats":formats,
            "has_rt":b"$OBD-RT" in data,"has_io":b"$OBD-IO" in data,
            "status":"catalogued_only"})
    target=ROOT/"profiles"
    target.mkdir(exist_ok=True)
    (target/"firmware-catalog.json").write_text(json.dumps({"schema":1,"source":"docs/EST580/固件版本",
        "warning":"字符串、向量与文件名仅用于筛选，不证明完整恢复或车型适用性。", "files":entries},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    sha="97cf73b6db26e9026900aeb91050d99c12952e909fe87a74c0c7f70c5b6af1d8"
    base={"channel":"can0","extended":False,"fd":False,"message_length":8,"factor":1,"offset":0,
          "origins":["firmware:"+sha],"source_sessions":[],
          "unresolved":["2019 固件对 2023 VA3 手动挡的适用性未验证","长度/过滤/失效码/超时/主动诊断依赖未完全追踪"],
          "freshness":0.5,"lag":0}
    rules=[]
    for name,ident,start,length,unit,semantic,post,extra in [
        ("EST_RPM",0x280,16,16,"rpm","rpm","floor",{"factor":0.25}),
        ("EST_Speed",0x320,40,16,"km/h","speed","va3_speed",{}),
        ("EST_SteeringPercent",0x0c2,0,16,"%","steering","va3_steering",{}),
        ("EST_DriverDoor",0x390,16,1,"","driver_door","none",{}),
        ("EST_Handbrake",0x320,9,1,"","handbrake","none",{}),
        ("EST_Brake",0x728,31,1,"","brake","none",{}),
        ("EST_LeftIndicator",0x392,11,1,"","left_indicator","va3_indicator_hold",{}),
        ("EST_RightIndicator",0x392,12,1,"","right_indicator","va3_indicator_hold",{})]:
        evidence={"EST_RPM":"0x080035CC–0x080035D8 → 结构 +8 → RT3; u >> 2",
                  "EST_Speed":"0x080035E2–0x080035F4 → 结构 +0xC → RT4; (u >> 1) // 100",
                  "EST_SteeringPercent":"0x0800354A–0x080035A2 → 结构 +0x15 → RT7; ((u & 0x3FFF)*100)//0x2E00; u > 0x8000 为 R 否则 L；百分比单位由用户确认，方向待实测",
                  "EST_DriverDoor":"0x08003626–0x08003636 → 结构 +0x49 → RT15; byte[2] & 1",
                  "EST_Handbrake":"0x08003608–0x08003620 → 结构 +0x59 → RT31; byte[1] & 2; formatter 0x08003E78/3E8E stack+120",
                  "EST_Brake":"ID = 0x48A+0x29E=0x728; 0x08003980–0x08003992 → 结构 +0x5A → RT32; byte[3] & 0x80; formatter 0x08003E6A/3E6E stack+124",
                  "EST_LeftIndicator":"0x08003734–0x08003768 → +0x5B → RT33; byte[1] bit3=1 置1且清计数；连续5次0清状态并清计数。初始值/外部写入/超时依赖仍待确认",
                  "EST_RightIndicator":"0x0800376A–0x080037A0 → +0x5C → RT34; byte[1] bit4=1 置1且清计数；连续5次0清状态并清计数。初始值/外部写入/超时依赖仍待确认"}[name]
        rules.append({**base,"name":name,"can_id":ident,"layout":{"start":start,"length":length,"endian":"little_endian","signed":False},
                      "unit":unit,"semantic":semantic,"postprocess":post,"evidence":evidence,**extra})
    (target/"va3-mt-2019.json").write_text(json.dumps({"schema":1,"firmware_sha256":sha,"status":"partial_static_candidates",
        "candidates":rules},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Indexed {len(entries)} unmodified firmware files; {len(rules)} partial candidates")


if __name__=="__main__":main()
