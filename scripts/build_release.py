"""Build on the target OS; no runtime installer or global environment changes."""
import argparse
import os
import shutil
import subprocess
import sys
import importlib.metadata
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def run(*args,cwd=ROOT,env=None): subprocess.run(list(map(str,args)),cwd=cwd,env=env,check=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--cache",type=Path,required=True)
    parser.add_argument("--skip-frontend",action="store_true")
    parser.add_argument("--dist",type=Path,default=ROOT/"dist")
    args=parser.parse_args()
    cache=args.cache.resolve();cache.mkdir(parents=True,exist_ok=True)
    if not args.skip_frontend:
        npm="npm.cmd" if os.name=="nt" else "npm"
        run(npm,"ci","--cache",cache/"npm","--no-audit","--no-fund",cwd=ROOT/"frontend")
        run(npm,"run","build",cwd=ROOT/"frontend")
    if not (ROOT/"frontend/dist/index.html").is_file(): raise SystemExit("先构建前端")
    cmake=cache/"cmake"
    command=["cmake","-S",ROOT/"collector","-B",cmake,"-DCMAKE_BUILD_TYPE=Release"]
    if os.name=="nt" and shutil.which("g++"): command.extend(["-G","MinGW Makefiles"])
    run(*command);run("cmake","--build",cmake,"--config","Release")
    filename="buspeeler-collector.exe" if os.name=="nt" else "buspeeler-collector"
    binary=cmake/filename
    if not binary.exists():binary=cmake/"Release"/filename
    dist=args.dist.resolve()
    build_env={**os.environ,"PYINSTALLER_CONFIG_DIR":str(cache/"pyinstaller-config")}
    run(sys.executable,"-m","PyInstaller","--noconfirm","--onedir","--name","Buspeeler",
        "--distpath",dist,"--workpath",cache/"pyinstaller","--specpath",cache,
        "--add-data",str(ROOT/"frontend/dist")+os.pathsep+"frontend/dist",
        "--add-data",str(ROOT/"profiles")+os.pathsep+"profiles",
        "--add-binary",str(binary)+os.pathsep+"collector",
        "--collect-all","cantools","--collect-submodules","uvicorn",
        ROOT/"run.py",env=build_env)
    package=dist/"Buspeeler"
    shutil.copy2(ROOT/"LICENSE",package/"LICENSE")
    shutil.copy2(ROOT/"docs/development.md",package/"使用与验收说明.md")
    shutil.copy2(ROOT/"requirements.lock",package/"requirements.lock")
    licenses=package/"licenses"
    for original in (Path(sys.base_prefix)/"LICENSE.txt",Path(sys.base_prefix)/"lib/python3.12/LICENSE.txt"):
        if original.is_file():
            licenses.mkdir(parents=True,exist_ok=True)
            shutil.copy2(original,licenses/"Python-LICENSE.txt")
    for distribution in importlib.metadata.distributions():
        for entry in distribution.files or []:
            if any(word in entry.name.lower() for word in ("license","copying","notice")):
                original=distribution.locate_file(entry)
                if original.is_file():
                    target=licenses/"python"/distribution.metadata["Name"]/str(entry)
                    if not target.resolve().is_relative_to(licenses.resolve()):continue
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(original,target)
    for original in (ROOT/"frontend/node_modules").rglob("*"):
        if original.is_file() and original.name.lower().startswith(("license","copying","notice")):
            target=licenses/"javascript"/original.relative_to(ROOT/"frontend/node_modules")
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,target)
    (package/"third-party-notices.txt").write_text(
        "Python and frontend dependency licenses are included in their distributed metadata.\n"
        "Frontend: Vue (MIT), Element Plus (MIT), ECharts (Apache-2.0).\n"
        "Vendor SDKs and source firmware are NOT included. Install licensed SDKs separately.\n",encoding="utf-8")
    print(f"Release directory: {package}")


if __name__=="__main__":main()
