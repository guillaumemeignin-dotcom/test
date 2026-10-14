"""On-disk JSON cache for API responses, keyed by endpoint + params (never the API key)."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

SECRET_PARAMS = {"apikey", "api_key", "token"}


class DiskCache:
    def __init__(self, root: str | Path, enabled: bool = True, refresh: bool = False):
        self.root = Path(root)
        self.enabled = enabled
        self.refresh = refresh

    @staticmethod
    def key(namespace: str, endpoint: str, params: dict[str, Any]) -> str:
        clean = {k: v for k, v in sorted(params.items()) if k.lower() not in SECRET_PARAMS}
        raw = json.dumps([namespace, endpoint, clean], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str, ttl_seconds: float) -> Any | None:
        if not self.enabled or self.refresh:
            return None
        path = self._path(key)
        try:
            with path.open() as fh:
                entry = json.load(fh)
        except (OSError, ValueError):
            return None
        if time.time() - entry.get("fetched_at", 0) > ttl_seconds:
            return None
        return entry.get("data")

    def set(self, key: str, data: Any, meta: dict[str, Any] | None = None) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with tmp.open("w") as fh:
            json.dump({"fetched_at": time.time(), "meta": meta or {}, "data": data}, fh)
        tmp.replace(path)
