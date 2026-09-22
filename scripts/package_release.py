"""Archive a tested release. Linux tar preserves only portable relative symlinks."""
import argparse
import hashlib
import tarfile
import zipfile
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--package",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    root=args.package.resolve();output=args.output.resolve()
    if not root.is_dir() or not (root/"_internal").is_dir():raise SystemExit("Not a release directory")
    if output.is_relative_to(root):raise SystemExit("Archive must be outside release directory")
    output.parent.mkdir(parents=True,exist_ok=True)
    if output.name.endswith(".zip"):
        with zipfile.ZipFile(output,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():archive.write(path,root.name+"/"+path.relative_to(root).as_posix())
    elif output.name.endswith(".tar.gz"):
        def portable(info):
            if info.issym() and (info.linkname.startswith("/") or not (root.parent/info.name).resolve().is_relative_to(root)):
                raise ValueError("Non-portable symlink: "+info.name)
            info.uid=info.gid=0;info.uname=info.gname=""
            info.mode=0o755 if info.isdir() or info.name.endswith(("/Buspeeler","/buspeeler-collector")) else 0o644
            return info
        with tarfile.open(output,"w:gz") as archive:archive.add(root,arcname=root.name,filter=portable)
    else:raise SystemExit("Use .zip or .tar.gz")
    sha=hashlib.file_digest(output.open("rb"),"sha256").hexdigest()
    output.with_name(output.name+".sha256").write_text(sha+"  "+output.name+"\n",encoding="utf-8")
    print(output.name,output.stat().st_size,sha)


if __name__=="__main__":main()
