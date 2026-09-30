from __future__ import annotations

import logging
import mimetypes
import os
import re
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Preview & Confronto: __TITLE__</title>
    <style>
        :root {
            --bg: #121214;
            --card-bg: #1e1e24;
            --text: #f0f0f5;
            --text-dim: #9ba1a6;
            --accent: #3b82f6;
            --accent-green: #10b981;
            --accent-yellow: #f59e0b;
            --border: #2e2e38;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            padding: 24px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .container {
            max-width: 1400px;
            width: 100%;
        }
        header {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px 24px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
        }
        h1 {
            font-size: 1.3rem;
            font-weight: 600;
        }
        .badges {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }
        .badge {
            background: #2a2a36;
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .badge.green { color: var(--accent-green); background: rgba(16, 185, 129, 0.15); }
        .badge.blue { color: var(--accent); background: rgba(59, 130, 246, 0.15); }
        .badge.yellow { color: var(--accent-yellow); background: rgba(245, 158, 11, 0.15); }
        
        .controls-bar {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 12px 20px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            font-size: 0.9rem;
        }
        .sync-toggle {
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
        }
        .sync-toggle input {
            width: 18px;
            height: 18px;
            cursor: pointer;
        }
        
        .players-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        @media (max-width: 900px) {
            .players-grid { grid-template-columns: 1fr; }
        }
        
        .player-card {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }
        .player-header {
            padding: 14px 18px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .player-title {
            font-weight: 600;
            font-size: 1.05rem;
        }
        .player-meta {
            font-size: 0.85rem;
            color: var(--text-dim);
        }
        .video-wrapper {
            background: #000;
            position: relative;
            width: 100%;
            aspect-ratio: 16 / 9;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        video {
            width: 100%;
            height: 100%;
            object-fit: contain;
        }
        .footer-note {
            margin-top: 24px;
            text-align: center;
            color: var(--text-dim);
            font-size: 0.9rem;
        }
        .btn {
            display: inline-block;
            padding: 8px 16px;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 500;
            font-size: 0.85rem;
            background: #2a2a36;
            color: var(--text);
            border: 1px solid var(--border);
            transition: background 0.2s;
        }
        .btn:hover { background: #383848; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>Confronto Video: __TITLE__</h1>
                <p style="color: var(--text-dim); font-size: 0.85rem; margin-top: 4px;">__SUBTITLE__</p>
            </div>
            <div class="badges">
                <span class="badge blue">__BADGE1__</span>
                <span class="badge green">__BADGE2__</span>
                __BADGE3_HTML__
            </div>
        </header>

        <div class="controls-bar">
            <label class="sync-toggle">
                <input type="checkbox" id="syncCheckbox" checked>
                <span>Sincronizza Riproduzione e Posizione temporale (Scrub)</span>
            </label>
            <div>
                <a href="/stream/file1?download=1" class="btn" download>Scarica __LABEL1__</a>
                <a href="/stream/file2?download=1" class="btn" download>Scarica __LABEL2__</a>
            </div>
        </div>

        <div class="players-grid">
            <div class="player-card">
                <div class="player-header">
                    <span class="player-title">__LABEL1__</span>
                    <span class="player-meta">__INFO1__</span>
                </div>
                <div class="video-wrapper">
                    <video id="video1" controls preload="auto" playsinline>
                        <source src="/stream/file1" type="__MIME1__">
                        <source src="/stream/file1">
                        Il tuo browser non supporta il tag video HTML5.
                    </video>
                </div>
            </div>

            <div class="player-card">
                <div class="player-header">
                    <span class="player-title">__LABEL2__</span>
                    <span class="player-meta">__INFO2__</span>
                </div>
                <div class="video-wrapper">
                    <video id="video2" controls preload="auto" playsinline>
                        <source src="/stream/file2" type="__MIME2__">
                        <source src="/stream/file2">
                        Il tuo browser non supporta il tag video HTML5.
                    </video>
                </div>
            </div>
        </div>

        <div class="footer-note">
            <p>💡 <strong>Istruzioni:</strong> Controlla la qualità del video, la fluidità e i colori. Se il browser non riesce a decodificare direttamente il codec originale (es. QuickTime HEVC senza estensioni), puoi scaricare il file con i pulsanti in alto o visualizzarlo su PhotoPrism. Quando hai finito, torna nel <strong>terminale</strong> per confermare o annullare l'operazione.</p>
        </div>
    </div>

    <script>
        const v1 = document.getElementById('video1');
        const v2 = document.getElementById('video2');
        const syncCheckbox = document.getElementById('syncCheckbox');
        let isSyncing = false;

        function syncVideos(source, target) {
            if (!syncCheckbox.checked || isSyncing) return;
            isSyncing = true;
            if (Math.abs(target.currentTime - source.currentTime) > 0.15) {
                target.currentTime = source.currentTime;
            }
            if (source.paused && !target.paused) {
                target.pause();
            } else if (!source.paused && target.paused) {
                target.play().catch(() => {});
            }
            setTimeout(() => { isSyncing = false; }, 50);
        }

        ['play', 'pause', 'seeking', 'seeked'].forEach(evt => {
            v1.addEventListener(evt, () => syncVideos(v1, v2));
            v2.addEventListener(evt, () => syncVideos(v2, v1));
        });

        // Periodic sync in case of drift
        v1.addEventListener('timeupdate', () => {
            if (!syncCheckbox.checked || isSyncing || v1.paused) return;
            if (Math.abs(v2.currentTime - v1.currentTime) > 0.3) {
                v2.currentTime = v1.currentTime;
            }
        });
    </script>
</body>
</html>
"""


class RangeRequestHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler supporting Range headers (HTTP 206) for video seeking."""

    server_context: dict[str, Any] = {}

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress standard noisy access logs
        return

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        if path in ("", "/preview"):
            self._serve_html()
            return

        if path == "/stream/file1":
            file_path = self.server_context.get("file1_path")
            self._serve_video(file_path, as_download=bool(query.get("download")))
            return

        if path == "/stream/file2":
            file_path = self.server_context.get("file2_path")
            self._serve_video(file_path, as_download=bool(query.get("download")))
            return

        self.send_error(404, "Not Found")

    def _serve_html(self) -> None:
        ctx = self.server_context
        badge3_html = f'<span class="badge yellow">{ctx.get("badge3")}</span>' if ctx.get("badge3") else ""

        html = (
            HTML_TEMPLATE
            .replace("__TITLE__", ctx.get("title", "Video Comparison"))
            .replace("__SUBTITLE__", ctx.get("subtitle", ""))
            .replace("__BADGE1__", ctx.get("badge1", ""))
            .replace("__BADGE2__", ctx.get("badge2", ""))
            .replace("__BADGE3_HTML__", badge3_html)
            .replace("__LABEL1__", ctx.get("label1", "File 1"))
            .replace("__LABEL2__", ctx.get("label2", "File 2"))
            .replace("__INFO1__", ctx.get("info1", ""))
            .replace("__INFO2__", ctx.get("info2", ""))
            .replace("__MIME1__", ctx.get("mime1", "video/mp4"))
            .replace("__MIME2__", ctx.get("mime2", "video/mp4"))
        )
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_video(self, file_path: str | None, as_download: bool = False) -> None:
        if not file_path or not os.path.isfile(file_path):
            self.send_error(404, "Video file not found")
            return

        total_size = os.path.getsize(file_path)
        mime_type, _ = mimetypes.guess_type(file_path)
        # Browsers play H.264/AAC MOV files best when Content-Type is video/mp4
        if not mime_type or not mime_type.startswith("video") or mime_type == "video/quicktime":
            mime_type = "video/mp4"

        range_header = self.headers.get("Range")
        if not range_header or as_download:
            # Full file
            self.send_response(200)
            self.send_header("Content-Type", mime_type)
            self.send_header("Content-Length", str(total_size))
            self.send_header("Accept-Ranges", "bytes")
            if as_download:
                filename = os.path.basename(file_path)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()

            with open(file_path, "rb") as f:
                self._copy_chunked(f, total_size)
            return

        # Handle Range: bytes=start-end
        range_match = re.match(r"^bytes=(\d*)-(\d*)$", range_header.strip())
        if not range_match:
            self.send_error(416, "Requested Range Not Satisfiable")
            return

        start_str, end_str = range_match.groups()
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else total_size - 1

        if start >= total_size or end >= total_size or start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total_size}")
            self.end_headers()
            return

        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Range", f"bytes {start}-{end}/{total_size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

        with open(file_path, "rb") as f:
            f.seek(start)
            self._copy_chunked(f, length)

    def _copy_chunked(self, f, length: int, chunk_size: int = 65536) -> None:
        remaining = length
        try:
            while remaining > 0:
                read_size = min(remaining, chunk_size)
                chunk = f.read(read_size)
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


class VideoPreviewServer:
    def __init__(self, port: int = 8765) -> None:
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(
        self,
        file1_path: str,
        file2_path: str,
        title: str,
        subtitle: str = "",
        label1: str = "Originale",
        label2: str = "Ottimizzato",
        info1: str = "",
        info2: str = "",
        badge1: str = "",
        badge2: str = "",
        badge3: str = "",
    ) -> str:
        """Start ephemeral preview server in a background thread and return access URL."""
        self.stop()

        context = {
            "file1_path": file1_path,
            "file2_path": file2_path,
            "title": title,
            "subtitle": subtitle,
            "label1": label1,
            "label2": label2,
            "info1": info1,
            "info2": info2,
            "badge1": badge1,
            "badge2": badge2,
            "badge3": badge3,
            "mime1": "video/mp4",
            "mime2": "video/mp4",
        }

        class CustomHandler(RangeRequestHandler):
            server_context = context

        # Allow port reuse
        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("0.0.0.0", self.port), CustomHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        host_ip = self._detect_host_ip()
        return f"http://{host_ip}:{self.port}"

    def stop(self) -> None:
        """Shutdown and terminate the preview server."""
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
            self._server = None
        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

    def _detect_host_ip(self) -> str:
        env_host = os.getenv("PREVIEW_HOST") or os.getenv("HOST_IP")
        if env_host:
            return env_host

        # Try to resolve outbound IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("192.168.1.1", 80))
            ip = s.getsockname()[0]
            s.close()
            # If inside docker network (e.g. 172.18.x.x), host IP is 192.168.1.100
            if ip.startswith("172.") or ip.startswith("10.") or ip.startswith("127."):
                return "192.168.1.100"
            return ip
        except Exception:
            return "192.168.1.100"
