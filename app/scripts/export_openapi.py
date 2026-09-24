"""Write the OpenAPI schema to docs/api/openapi.json (for frontend TypeScript codegen).

python -m app.scripts.export_openapi
"""

import json
from pathlib import Path

from app.main import app

OUT = Path("docs/api/openapi.json")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
