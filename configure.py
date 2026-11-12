"""Print integration configuration without editing the user's agent settings."""
import argparse
import json
from pathlib import Path
import sys


def configuration(root, hermes=False, evidence_dir=None):
    archive = getattr(globals().get("__loader__"), "archive", None)
    launcher = [str(Path(archive).resolve()), "mcp"] if archive else ["-m", "mcp_server"]
    args = ["-B", *launcher, "--root", str(Path(root).resolve())]
    if evidence_dir:
        args += ["--evidence-dir", str(Path(evidence_dir).resolve())]
    return {"mcp_servers" if hermes else "mcpServers": {"checkgate": {"command": sys.executable, "args": args}}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--hermes", action="store_true", help="Use Hermes's mcp_servers configuration key")
    args = parser.parse_args()
    print(json.dumps(configuration(args.root, args.hermes, args.evidence_dir), indent=2))
