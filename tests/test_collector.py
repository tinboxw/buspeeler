import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest


def test_vendor_receive_only_abi_and_dual_channel(tmp_path):
    compiler=shutil.which("g++")
    if not compiler:pytest.skip("C++ compiler unavailable")
    root=Path(__file__).resolve().parents[1]
    collector=tmp_path/("collector.exe" if os.name=="nt" else "collector")
    library=tmp_path/("mock.dll" if os.name=="nt" else "mock.so")
    static=["-static"] if os.name=="nt" else ["-ldl","-pthread"]
    subprocess.run([compiler,"-std=c++17",str(root/"collector/main.cpp"),"-o",str(collector),*static],check=True,capture_output=True)
    subprocess.run([compiler,"-shared",str(root/"tests/mock_controlcan.cpp"),"-o",str(library),*(["-static"] if os.name=="nt" else ["-fPIC"])],check=True,capture_output=True)
    args=[str(collector),"backend","zlg","library",str(library),"verified","1","type","4","index","0","channel","-1","timing0","0","timing1","28"]
    process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        time.sleep(.4)
        output,errors=process.communicate("stop\n",timeout=5)
        assert process.returncode==0,errors
        rows=[json.loads(line) for line in output.splitlines()]
        assert {r["channel"] for r in rows}=={"can0","can1"}
        assert all(r["data"]=="0000010000000000" and r["dropped"] is None for r in rows)
        args[args.index("verified")+1]="0"
        blocked=subprocess.run(args,input="stop\n",text=True,capture_output=True)
        assert blocked.returncode==2 and "qualification" in blocked.stderr
    finally:
        if process.poll() is None:process.kill();process.wait()
