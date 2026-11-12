"""Build a single-file Python zipapp from the verifier and its thin adapters."""
import argparse
from pathlib import Path
import zipfile

FILES = ("checkgate.py", "service.py", "mcp_server.py", "hermes_hook.py", "configure.py", "demo.py")


def build(output):
    source = Path(__file__).resolve().parent
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as package:
        for name in FILES:
            package.write(source / name, name)
        package.write(source / "entrypoint.py", "__main__.py")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="dist/checkgate.pyz")
    print(build(parser.parse_args().output))
