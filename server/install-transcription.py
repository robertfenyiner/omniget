#!/usr/bin/env python3
"""Build a pinned whisper.cpp and verify its multilingual model before use."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

TAG = "v1.9.5"
COMMIT = "d1be6fde11ac6e0407606b4e42fe72d34add8037"
MODEL_REVISION = "5359861c739e955e79d9a303bcbc70fb988958b1"
MODELS = {
    "base-q5_1": "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898",
    "small-q5_1": "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=MODELS, default="base-q5_1")
    args = parser.parse_args()
    os.umask(0o077)
    for tool in ("git", "cmake", "c++"):
        if not shutil.which(tool):
            raise SystemExit(f"Falta {tool}. Instala git, cmake y build-essential antes de continuar.")
    tools = Path.home() / ".local/share/omniget-tools"
    tools.mkdir(parents=True, exist_ok=True)
    source, build = tools / "whisper-source", tools / "whisper-build"
    if not source.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", TAG, "--no-recurse-submodules",
                        "https://github.com/ggml-org/whisper.cpp.git", str(source)], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
    if revision != COMMIT or dirty:
        raise SystemExit("El código fuente no coincide con la revisión limpia esperada. No se compilará.")
    subprocess.run(["cmake", "-S", str(source), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                    "-DBUILD_SHARED_LIBS=OFF", "-DWHISPER_BUILD_TESTS=OFF", "-DGGML_OPENMP=OFF"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--target", "whisper-cli", "-j2"], check=True)
    binaries = Path.home() / ".local/bin"
    binaries.mkdir(parents=True, exist_ok=True)
    target = binaries / "whisper-cli"
    staged = binaries / "whisper-cli.new"
    shutil.copy2(build / "bin/whisper-cli", staged)
    staged.chmod(0o700)
    staged.replace(target)
    data = Path(os.environ.get("OMNIGET_WEB_DATA", str(Path.home() / ".local/share/omniget-web")))
    models = data / "models/whisper"
    models.mkdir(parents=True, exist_ok=True)
    name = f"ggml-{args.model}.bin"
    destination = models / name
    temporary = destination.with_suffix(".part")
    if not destination.exists():
        url = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/{MODEL_REVISION}/{name}"
        with urllib.request.urlopen(url, timeout=90) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        verify = temporary
    else:
        verify = destination
    with verify.open("rb") as model:
        actual = hashlib.file_digest(model, "sha256").hexdigest()
    if actual != MODELS[args.model]:
        temporary.unlink(missing_ok=True)
        if destination.exists():
            destination.replace(destination.with_suffix(f".corrupt-{time.time_ns()}"))
        raise SystemExit("El SHA-256 del modelo no coincide. No se habilitará.")
    if temporary.exists():
        temporary.replace(destination)
    print(f"Motor instalado: {target}; modelo verificado: {destination}")
    print("Reinicia omniget-web para detectar el motor y el modelo.")


if __name__ == "__main__":
    main()
