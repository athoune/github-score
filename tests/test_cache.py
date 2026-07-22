"""Tests for cache module."""

import pytest
import tempfile
from pathlib import Path

from gh_score.core.cache import Cache


class TestCache:
    def test_set_and_get(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir)
            cache.set("test_key", b"test_data")
            assert cache.get("test_key") == b"test_data"

    def test_get_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir)
            assert cache.get("nonexistent") is None

    def test_ttl_expiration(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir, default_ttl_seconds=1)
            cache.set("test_key", b"test_data", ttl_seconds=1)
            assert cache.get("test_key") == b"test_data"

            # Wait for expiration
            import time
            time.sleep(1.5)
            assert cache.get("test_key") is None

    def test_invalidate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir)
            cache.set("test_key", b"test_data")
            cache.invalidate("test_key")
            assert cache.get("test_key") is None

    def test_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir)
            cache.set("key1", b"data1")
            cache.set("key2", b"data2")
            cache.clear()
            assert cache.get("key1") is None
            assert cache.get("key2") is None

    def test_json_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = Cache(tmpdir)
            data = {"foo": "bar", "count": 42}
            cache.set_json("json_key", data)
            result = cache.get_json("json_key")
            assert result == data
