"""Export the Pydantic graph schema used by API and web clients."""

import json
from pathlib import Path

from app.graph import Graph


def main() -> None:
    target = Path("packages/workflow-schema/graph-v1.schema.json")
    target.write_text(json.dumps(Graph.model_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
