"""Read only crosswalk members of the official Scorecard archive using HTTP ranges."""
import hashlib
import io
import json
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

URL = "https://ed-public-download.scorecard.network/downloads/College_Scorecard_Raw_Data_06102026.zip"
OUT = Path("ResearchBuild/IPEDS/official_crosswalks")


class RemoteArchive(io.RawIOBase):
    def __init__(self, url):
        self.url = url
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=0.2)))
        response = self.session.head(url, timeout=45)
        response.raise_for_status()
        self.length = int(response.headers["Content-Length"])
        self.headers = dict(response.headers)
        self.position = 0
        self.requests = []

    def seekable(self):
        return True

    def readable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position + offset if whence == 1 else self.length + offset
        return self.position

    def read(self, size=-1):
        end = self.length - 1 if size < 0 else min(self.position + size - 1, self.length - 1)
        if end < self.position:
            return b""
        headers = {"Range": f"bytes={self.position}-{end}", "Accept-Encoding": "identity"}
        response = self.session.get(self.url, headers=headers, timeout=45, stream=True)
        response.raise_for_status()
        if response.status_code != 206:
            response.close()
            raise RuntimeError("Server ignored Range; refusing to download entire archive")
        data = response.content
        if len(data) != end - self.position + 1:
            raise RuntimeError("Range length mismatch")
        if response.headers.get("ETag") != self.headers.get("ETag"):
            raise RuntimeError("Remote archive changed during extraction")
        self.requests.append({"start": self.position, "end": end, "bytes": len(data), "content_range": response.headers.get("Content-Range")})
        self.position = end + 1
        return data


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    remote = RemoteArchive(URL)
    records = []
    with zipfile.ZipFile(remote) as archive:
        listing = [{"name": i.filename, "bytes": i.file_size, "compressed_bytes": i.compress_size, "crc32": i.CRC} for i in archive.infolist()]
        (OUT / "archive_listing.json").write_text(json.dumps(listing, indent=2) + "\n")
        chosen = [i for i in archive.infolist() if "crosswalk" in i.filename.lower()
                  and Path(i.filename).name.startswith("CW") and i.filename.lower().endswith((".xlsx", ".xls"))]
        print("Crosswalk archive members:", len(chosen), flush=True)
        for item in chosen:
            target = OUT / Path(item.filename).name
            data = target.read_bytes() if target.exists() else b""
            if len(data) != item.file_size or zlib.crc32(data) != item.CRC:
                data = archive.read(item)
            target.write_bytes(data)
            records.append({"member": item.filename, "path": str(target.resolve()), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "crc32": item.CRC})
            print(item.filename, len(data), flush=True)
    manifest = {"official_listing_url": "https://collegescorecard.ed.gov/data/", "archive_url": URL,
                "retrieved_utc": datetime.now(timezone.utc).isoformat(), "archive_bytes": remote.length,
                "archive_etag": remote.headers.get("ETag"), "archive_last_modified": remote.headers.get("Last-Modified"),
                "archive_s3_version_id": remote.headers.get("x-amz-version-id"),
                "method": "HTTP byte ranges; zipfile member decompression and CRC validation; archive not downloaded in full",
                "range_requests": remote.requests, "members": records}
    (OUT / "download_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Transferred bytes:", sum(r["bytes"] for r in remote.requests), flush=True)


if __name__ == "__main__":
    main()
