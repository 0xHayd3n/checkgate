import ast
from pathlib import Path
for path in Path(".").glob("*.py"):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
