"""Persistent cache for HTTP responses and analysis results.

Uses filesystem-based storage with TTL support.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class Cache:
    """Filesystem-based cache with TTL support."""

    def __init__(self, cache_dir: str, default_ttl_seconds: int = 86400):
        """Initialize cache.

        Args:
            cache_dir: Directory for cache storage
            default_ttl_seconds: Default time-to-live in seconds (24h)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl_seconds

    def _key_to_path(self, key: str) -> tuple[Path, Path]:
        """Convert cache key to file paths (data + metadata)."""
        # Hash the key to create a safe filename
        key_hash = hashlib.sha256(key.encode()).hexdigest()[:32]
        data_path = self.cache_dir / f"{key_hash}.data"
        meta_path = self.cache_dir / f"{key_hash}.meta"
        return data_path, meta_path

    def get(self, key: str) -> bytes | None:
        """Get cached data if it exists and hasn't expired.

        Returns None if not found or expired.
        """
        data_path, meta_path = self._key_to_path(key)

        if not data_path.exists() or not meta_path.exists():
            return None

        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)

            # Check TTL
            expires_at = meta.get("expires_at", 0)
            if time.time() > expires_at:
                # Expired, remove files
                data_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                return None

            with open(data_path, "rb") as f:
                return f.read()
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, key: str, data: bytes, ttl_seconds: int | None = None) -> None:
        """Store data in cache with TTL.

        Args:
            key: Cache key
            data: Data to cache
            ttl_seconds: Time-to-live in seconds (uses default if None)
        """
        data_path, meta_path = self._key_to_path(key)
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl

        try:
            # Write data
            with open(data_path, "wb") as f:
                f.write(data)

            # Write metadata
            meta = {
                "key": key,
                "created_at": time.time(),
                "expires_at": time.time() + ttl,
                "size": len(data),
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f)
        except OSError:
            # Silently fail on write errors
            pass

    def invalidate(self, key: str) -> None:
        """Remove a specific key from cache."""
        data_path, meta_path = self._key_to_path(key)
        data_path.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)

    def clear(self) -> None:
        """Clear all cached data."""
        for pattern in ["*.data", "*.meta"]:
            for path in self.cache_dir.glob(pattern):
                path.unlink(missing_ok=True)

    def get_json(self, key: str) -> Any | None:
        """Get cached JSON data."""
        data = self.get(key)
        if data is None:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def set_json(self, key: str, data: Any, ttl_seconds: int | None = None) -> None:
        """Store JSON data in cache."""
        self.set(key, json.dumps(data).encode(), ttl_seconds)
