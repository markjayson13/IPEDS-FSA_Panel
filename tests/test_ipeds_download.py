"""Offline regression tests for official endpoint vintage selection."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from fsa_ipeds_linkage import download_ipeds_sources, NCES_BASE, NCES_LEGACY_BASE


def archive_bytes():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("FA2000HD.csv", "UNITID,OPEID\n100654,00100200\n")
    return data.getvalue()


def response(status, url, content=b""):
    result = Mock(status_code=status, url=url, content=content, headers={})
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(f"HTTP {status}")
    return result


class OfficialDownloadTests(unittest.TestCase):
    def test_current_endpoint_is_preferred_and_recorded(self):
        url = NCES_BASE + "FA2000HD.zip"
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get", return_value=response(200, url, archive_bytes())) as get:
            result = download_ipeds_sources(folder, [2000, 2000])
            self.assertEqual(len(result), 1)
            get.assert_called_once_with(url, timeout=45)
            self.assertEqual(result.iloc[0]["url"], url)
            self.assertEqual(result.iloc[0]["status"], "downloaded")
            self.assertEqual(result.iloc[0]["source_vintage_resolution"], "current_complete_data_files_endpoint")

    def test_only_explicit_404_allows_legacy_fallback(self):
        current, legacy = NCES_BASE + "FA2000HD.zip", NCES_LEGACY_BASE + "FA2000HD.zip"
        replies = [response(404, current), response(200, legacy, archive_bytes())]
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get", side_effect=replies) as get:
            row = download_ipeds_sources(folder, [2000]).iloc[0]
            self.assertEqual(get.call_count, 2)
            self.assertEqual(row["url"], legacy)
            self.assertEqual([r["http_status"] for r in row["request_attempts"]], [404, 200])
            self.assertIn("legacy_archive_fallback", row["source_vintage_resolution"])

    def test_access_error_does_not_silently_select_older_vintage(self):
        current = NCES_BASE + "FA2000HD.zip"
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get", return_value=response(403, current)) as get:
            row = download_ipeds_sources(folder, [2000]).iloc[0]
            self.assertEqual(row["status"], "failed")
            get.assert_called_once()
            self.assertFalse((Path(folder) / "FA2000HD.zip").exists())

    def test_cached_hash_mismatch_is_explicit_and_does_not_rewrite_source(self):
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get") as get:
            path = Path(folder) / "FA2000HD.zip"
            path.write_bytes(archive_bytes())
            path.with_suffix(".download.json").write_text(json.dumps({"sha256": "incorrect", "url": "legacy-origin"}))
            original = path.read_bytes()
            row = download_ipeds_sources(folder, [2000]).iloc[0]
            self.assertEqual(row["status"], "failed")
            self.assertFalse(row["download_metadata_hash_matches"])
            self.assertEqual(path.read_bytes(), original)
            get.assert_not_called()

    def test_cached_valid_legacy_origin_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get") as get:
            path = Path(folder) / "FA2000HD.zip"
            original = archive_bytes()
            path.write_bytes(original)
            path.with_suffix(".download.json").write_text(json.dumps({"sha256": hashlib.sha256(original).hexdigest(), "url": "legacy-origin"}))
            row = download_ipeds_sources(folder, [2000]).iloc[0]
            self.assertEqual(row["status"], "cached")
            self.assertEqual(row["url"], "legacy-origin")
            get.assert_not_called()

    def test_invalid_refresh_preserves_previous_valid_archive(self):
        with tempfile.TemporaryDirectory() as folder, patch("fsa_ipeds_linkage.requests.get", return_value=response(200, NCES_BASE + "FA2000HD.zip", b"HTML error")):
            path = Path(folder) / "FA2000HD.zip"
            original = archive_bytes()
            path.write_bytes(original)
            row = download_ipeds_sources(folder, [2000], refresh=True).iloc[0]
            self.assertEqual(row["status"], "failed")
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
