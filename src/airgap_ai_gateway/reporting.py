"""Report formatting helpers."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, cast


def to_json(data: object) -> str:
    """Serialize reports in a deterministic shape."""

    if hasattr(data, "__dataclass_fields__") and not isinstance(data, type):
        payload: Any = asdict(cast(Any, data))
    else:
        payload = data
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"
