"""Write the OpenAPI contract the Dart client is generated from.

    uv run python scripts/export_openapi.py

tests/test_openapi.py fails when the committed file is out of date.
"""

import json
from pathlib import Path

from guzo.main import create_app

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi" / "guzo-v1.json"


def render() -> str:
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SPEC_PATH.write_text(render())
    print(f"wrote {SPEC_PATH}")
