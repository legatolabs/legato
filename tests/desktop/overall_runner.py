"""Run the pinned, CPU-only local model and the paired request-to-file benchmark on a dedicated desktop."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

RELEASE="https://github.com/ggml-org/llama.cpp/releases/download/b12774/llama-b12774-bin-"
PLATFORM={
 "darwin":("macos-arm64.tar.gz","70311c4751ed3221e63f47769327dd088d7400f76dcfb81c66ec843db165814d"),
 "linux":("ubuntu-x64.tar.gz","ab5837cca2317cfb7ecb5f85b550934e7355cd9df74cdde6e106581457034dbb"),
 "win32":("win-cpu-x64.zip","44b70b20f3374eec3d16a002beceefef33cc3565678caeb3eea5f6cacba61836")}
MODEL="https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/7dabda4d13d513e3e842b20f0d435c732f172cbe/qwen2.5-3b-instruct-q4_k_m.gguf"
MODEL_HASH="626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"


def fetch(url,path,digest):
    for attempt in range(3):
        try:
            subprocess.run(["curl","-fLsS","--connect-timeout","20","--max-time","500","-o",str(path),url],check=True)
            with path.open("rb") as src:
                if hashlib.file_digest(src,"sha256").hexdigest()!=digest:raise ValueError("download digest mismatch")
            return
        except (ValueError,subprocess.CalledProcessError):
            if attempt==2:raise


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--shard",required=True);args=ap.parse_args()
    with tempfile.TemporaryDirectory() as d:
        folder=Path(d);name,digest=PLATFORM[sys.platform]
        archive=folder/name;fetch(RELEASE+name,archive,digest)
        if name.endswith("zip"):
            with zipfile.ZipFile(archive) as z:z.extractall(folder)
        else:
            with tarfile.open(archive,"r:gz") as z:z.extractall(folder,filter="data")
        binary=next(folder.rglob("llama-server.exe" if sys.platform=="win32" else "llama-server"))
        model=folder/"model.gguf";fetch(MODEL,model,MODEL_HASH)
        env={**os.environ,"LD_LIBRARY_PATH":str(binary.parent),"DYLD_LIBRARY_PATH":str(binary.parent)}
        with (folder/"server.log").open("w") as log:
            proc=subprocess.Popen([str(binary),"-m",str(model),"--host","127.0.0.1","--port","18714","--device","none",
                        "-ngl","0","-t","3","-tb","3","-c","2048","-np","1","-a","legato-overall"],
                        stdout=log,stderr=log,env=env,cwd=binary.parent)
            try:
                deadline=time.monotonic()+90
                while time.monotonic()<deadline and proc.poll() is None:
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:18714/health",timeout=2) as r:
                            if json.load(r).get("status")=="ok":break
                    except OSError:time.sleep(.5)
                else:
                    print((folder/"server.log").read_text(errors="replace")[-2200:])
                    raise RuntimeError("local model failed startup")
                subprocess.run([sys.executable,"-u",str(Path(__file__).with_name("conditional_loop.py")),
                                "--shard",args.shard],check=True)
            finally:
                proc.terminate();proc.wait(timeout=20)


if __name__=="__main__":main()
