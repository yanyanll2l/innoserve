'''
fetch.py 負責從網路下載官方資料。
可以下載：
    - JSON
    - CSV
    - HTML
    - PDF
它也處理：
    - 網路逾時
    - SSL 憑證問題
    - 重新嘗試下載
    - 中文編碼
'''

from __future__ import annotations

import time
import urllib.error
import urllib.request
import ssl
from dataclasses import dataclass


USER_AGENT = "WelfareNavigationMVP/0.1 (+student project; public-data reader)"


def _government_ssl_context() -> ssl.SSLContext:
    """保留憑證鏈／主機名稱驗證，避開部分政府站舊憑證的 OpenSSL strict 相容性問題。"""
    context = ssl.create_default_context()
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        context.verify_flags &= ~strict_flag
    return context


@dataclass(slots=True)
class FetchResponse:
    url: str
    body: bytes
    content_type: str
    charset: str | None
    etag: str | None
    last_modified: str | None

    def text(self) -> str:
        encodings = [self.charset, "utf-8-sig", "utf-8", "cp950", "big5"]
        for encoding in encodings:
            if not encoding:
                continue
            try:
                return self.body.decode(encoding)
            except (LookupError, UnicodeDecodeError):
                continue
        return self.body.decode("utf-8", errors="replace")


def fetch(url: str, timeout: float = 25.0, retries: int = 2) -> FetchResponse:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json,text/csv,text/html,application/xhtml+xml,*/*;q=0.8",
        },
    )
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=timeout, context=_government_ssl_context()
            ) as response:
                content_type = response.headers.get_content_type()
                charset = response.headers.get_content_charset()
                return FetchResponse(
                    url=response.geturl(),
                    body=response.read(),
                    content_type=content_type,
                    charset=charset,
                    etag=response.headers.get("ETag"),
                    last_modified=response.headers.get("Last-Modified"),
                )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
            if attempt < retries:
                time.sleep(0.6 * (attempt + 1))
    assert last_error is not None
    raise RuntimeError(f"無法下載 {url}: {last_error}") from last_error
