"""Downloads against a local HTTP server that drops connections, ignores ranges, or serves the wrong bytes."""

import hashlib
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from vaquero import remote

BODY = bytes(range(256)) * 16384  # 4 MiB: several of remote.CHUNK
DIGEST = "sha256:" + hashlib.sha256(BODY).hexdigest()


class Server:
    """Serves BODY at /good, honouring Range; /drop cuts the first request off halfway; /404 is missing;
    /wrong serves other bytes of the same length."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.dropped = False
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                rng = self.headers.get("Range", "")
                server.requests.append((self.path, rng))
                if self.path == "/404":
                    self.send_error(404)
                    return
                body = bytes(len(BODY)) if self.path == "/wrong" else BODY
                start = int(rng.removeprefix("bytes=").rstrip("-")) if rng else 0
                self.send_response(206 if start else 200)
                self.send_header("Content-Length", str(len(body) - start))
                self.end_headers()
                if self.path == "/drop" and not server.dropped:
                    server.dropped = True
                    self.wfile.write(body[start : len(body) // 2])
                    self.wfile.flush()
                    self.connection.close()
                    return
                self.wfile.write(body[start:])

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Server]:
    monkeypatch.setattr(remote, "DOWNLOADS_DIR", tmp_path / "downloads")
    monkeypatch.setattr(remote, "_backoff", lambda attempt: 0.0)
    s = Server()
    yield s
    s.httpd.shutdown()


def asset(server: Server, path: str, *mirrors: str, digest: str = DIGEST) -> remote.Asset:
    return remote.Asset("pack.zip", len(BODY), server.url + path, digest, tuple(server.url + m for m in mirrors))


def test_a_dropped_connection_is_retried_and_resumes_where_it_stopped(server: Server) -> None:
    got = remote.download(asset(server, "/drop"), lambda done, total: None)

    assert got.read_bytes() == BODY
    assert server.requests == [("/drop", ""), ("/drop", f"bytes={len(BODY) // 2}-")]


def test_a_missing_file_falls_back_to_the_mirror(server: Server) -> None:
    got = remote.download(asset(server, "/404", "/good"), lambda done, total: None)

    assert got.read_bytes() == BODY
    assert [p for p, _ in server.requests] == ["/404", "/good"]


def test_wrong_bytes_are_discarded_and_never_installed(server: Server) -> None:
    with pytest.raises(remote.RemoteError, match="checksum"):
        remote.download(asset(server, "/wrong"), lambda done, total: None)

    assert len(server.requests) == remote.DOWNLOAD_ATTEMPTS
    assert not list((remote.DOWNLOADS_DIR).iterdir())


def test_a_paused_download_is_not_retried_and_keeps_its_partial_file(server: Server) -> None:
    with pytest.raises(remote.DownloadPaused):
        remote.download(asset(server, "/good"), lambda done, total: None, cancelled=lambda: True)

    assert len(server.requests) == 1
    kept = (remote.DOWNLOADS_DIR / "pack.zip.part").stat().st_size
    assert 0 < kept < len(BODY)

    got = remote.download(asset(server, "/good"), lambda done, total: None)
    assert got.read_bytes() == BODY
    assert server.requests[-1] == ("/good", f"bytes={kept}-")


def test_backoff_grows_and_is_capped() -> None:
    waits = [remote._backoff(n) for n in range(8)]
    assert waits[0] < waits[1] < waits[2]
    assert all(w <= remote.BACKOFF_CAP_S + 1 for w in waits)


def test_a_cut_short_model_file_is_detected(tmp_path: Path) -> None:
    pack = tmp_path / "buffalo_l"
    pack.mkdir()
    for name, (size, _) in remote.MODEL_FILES.items():
        with (pack / name).open("wb") as fh:
            fh.truncate(size)
    assert remote.pack_intact(pack)

    with (pack / "w600k_r50.onnx").open("r+b") as fh:
        fh.truncate(1000)
    assert not remote.pack_intact(pack)
    (pack / "det_10g.onnx").unlink()
    assert not remote.pack_intact(pack)


def test_the_model_pin_points_at_this_repo_with_upstream_fallbacks() -> None:
    m = remote.MODEL_ARCHIVE
    assert m.url.startswith(f"https://github.com/{remote.REPO}/releases/download/model-")
    assert all("deepinsight/insightface" in u for u in m.mirrors)
    assert m.digest.startswith("sha256:") and m.size > 0
