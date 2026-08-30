import os
import shutil
import subprocess
import sys
from pathlib import Path

def compile_protos():
    base_dir = Path(__file__).parent.resolve()
    proto_dir = base_dir / "proto"
    out_dir = base_dir / "generated"

    # Remove old generated dir if exists
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(exist_ok=True)
    (out_dir / "__init__.py").touch()

    # Find all .proto files under proto_dir
    proto_files = list(proto_dir.rglob("*.proto"))
    print(f"Found {len(proto_files)} proto files to compile.")

    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"--python_out={out_dir}",
        f"--grpc_python_out={out_dir}",
    ] + [str(p) for p in proto_files]

    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    print("Compilation finished successfully.")

    # Create __init__.py in all subdirectories of generated
    for root, dirs, files in os.walk(out_dir):
        init_file = Path(root) / "__init__.py"
        init_file.touch()

if __name__ == "__main__":
    compile_protos()
