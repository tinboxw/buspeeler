"""Exercise a running packaged application's release gates with synthetic fixtures."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_core import test_independent_validation_and_immutable_publication


if __name__=="__main__":
    info=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    url=urlsplit(info["url"])
    token=parse_qs(url.fragment)["token"][0]
    with httpx.Client(base_url=f"{url.scheme}://{url.netloc}",headers={"Authorization":"Bearer "+token},timeout=60) as client:
        test_independent_validation_and_immutable_publication(client)
    print("Packaged HTTP gates passed: independent validation, DBC publication, immutable revisions; synthetic fixtures only.")
