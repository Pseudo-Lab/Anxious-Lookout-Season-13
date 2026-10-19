"""Print the versioned block input schema from Pydantic shapes; no application writes."""
import json
from app.post_block_contract import export_schema

if __name__ == '__main__':
    print(json.dumps(export_schema(), ensure_ascii=False, indent=2, sort_keys=True))
