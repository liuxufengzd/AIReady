#!/usr/bin/env python3
"""
Script to generate Python code from proto files.

Usage:
    python scripts/generate_proto.py

This script can be run from anywhere:
    python backend/search/scripts/generate_proto.py

Or from the search directory:
    cd backend/search
    python scripts/generate_proto.py
"""

import subprocess
import sys
from pathlib import Path


def main():
    # Get the backend directory (proto files are now at backend/grpc_protos/search)
    search_dir = Path(__file__).parent.parent.resolve()
    backend_dir = search_dir.parent
    proto_dir = backend_dir / "grpc_protos" / "search"

    proto_file = proto_dir / "search.proto"

    if not proto_file.exists():
        print(f"Error: Proto file not found at {proto_file}")
        sys.exit(1)

    print(f"Generating Python code from {proto_file}...")

    # Run protoc with grpc plugin and type stub generation
    cmd = [
        sys.executable,
        "-m",
        "grpc_tools.protoc",
        f"-I{proto_dir}",
        f"--python_out={proto_dir}",
        f"--pyi_out={proto_dir}",  # Generate .pyi type stubs for IDE support
        f"--grpc_python_out={proto_dir}",
        str(proto_file),
    ]

    try:
        subprocess.run(cmd, check=True)
        print("Proto files generated successfully!")

        # Fix imports in the generated grpc file
        # grpc_tools generates "import search_pb2" but we need the correct import path
        grpc_file = proto_dir / "search_pb2_grpc.py"
        if grpc_file.exists():
            content = grpc_file.read_text()
            fixed_content = content.replace(
                "import search_pb2", "from grpc_protos.search import search_pb2"
            )
            grpc_file.write_text(fixed_content)
            print("Fixed imports in search_pb2_grpc.py")

        # Fix imports in the generated pyi file
        pyi_file = proto_dir / "search_pb2.pyi"
        if pyi_file.exists():
            content = pyi_file.read_text()
            # Fix any potential import issues in the stub file
            fixed_content = content.replace(
                "import search_pb2", "from grpc_protos.search import search_pb2"
            )
            pyi_file.write_text(fixed_content)
            print("Fixed imports in search_pb2.pyi")

        print("\nGenerated files:")
        print(f"  - {proto_dir / 'search_pb2.py'}")
        print(f"  - {proto_dir / 'search_pb2.pyi'} (type stubs for IDE)")
        print(f"  - {proto_dir / 'search_pb2_grpc.py'}")
        print("\nDone!")

    except subprocess.CalledProcessError as e:
        print(f"Error generating proto files: {e}")
        sys.exit(1)
    except FileNotFoundError:
        print("Error: grpc_tools not installed. Run: pip install grpcio-tools")
        sys.exit(1)


if __name__ == "__main__":
    main()
