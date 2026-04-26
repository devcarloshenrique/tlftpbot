"""
main.py — Ponto de entrada do FastAPI para o TL-Stream.

Endpoints:
    GET /              → Página HTML (directory index) para o Rclone parsear.
    GET /fetch-movies  → JSON com todos os filmes e seus chunks.
    GET /{filename}    → Streaming de vídeo com suporte a HTTP Range (206).
"""

from __future__ import annotations

import logging
import mimetypes
from contextlib import asynccontextmanager
from os.path import exists
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response

if exists(".env"):
    from dotenv import load_dotenv
    load_dotenv()

from app import telegram_api
from app.stream_utils import (
    calculate_total_size,
    parse_range_header,
    stream_file_range,
)

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
)
logger = logging.getLogger("TLStream")


# ── Lifespan (startup / shutdown) ────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa o bot do Telegram e o MongoDB no startup; encerra no shutdown."""
    logger.info("🚀 TL-Stream: Iniciando serviços...")
    await telegram_api.init_db()
    await telegram_api.init_bot()
    logger.info("✅ TL-Stream pronto para servir.")
    yield
    logger.info("⏳ TL-Stream: Encerrando...")
    await telegram_api.stop_bot()
    logger.info("👋 TL-Stream encerrado.")


app = FastAPI(
    title="TL-Stream",
    description="HTTP bridge para streaming de vídeos armazenados no Telegram",
    version="1.0.0",
    lifespan=lifespan,
)


# ── GET / — Directory index HTML (para o Rclone) ────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    """
    Retorna uma página HTML que imita um directory index Apache/Nginx.
    O Rclone 'http' backend parseia tags <a href="..."> relativas.
    """
    movies = await telegram_api.fetch_movies()

    lines = []
    for movie in movies:
        name = movie["name"]
        size = movie["size"]
        encoded_name = quote(name)
        size_mb = f"{size / (1024 * 1024):.0f}M" if size > 0 else "0M"
        # Formato estritamente compatível com Rclone: link relativo + data + tamanho
        lines.append(
            f'<a href="{encoded_name}">{name}</a>             26-Apr-2026 00:00  {size_mb}'
        )

    entries = "\n".join(lines)

    html = (
        '<!DOCTYPE html>\n'
        '<html>\n'
        '<head><title>Index of /</title></head>\n'
        '<body>\n'
        '<h1>Index of /</h1><hr><pre>\n'
        f'{entries}\n'
        '</pre><hr>\n'
        '</body>\n'
        '</html>'
    )

    return HTMLResponse(content=html)


# ── GET /fetch-movies — JSON de filmes e chunks ─────────────────────

@app.get("/fetch-movies")
async def fetch_movies_endpoint():
    """
    Retorna JSON com todos os filmes e seus arrays de chunks.
    Usado para inspeção e debug.
    """
    movies = await telegram_api.fetch_movies()

    result = []
    for movie in movies:
        result.append({
            "name": movie["name"],
            "size": movie["size"],
            "parent": movie["parent"],
            "chunks": [
                {
                    "part_id": p["part_id"],
                    "tg_message": p["tg_message"],
                    "file_size": p["file_size"],
                }
                for p in movie["parts"]
            ],
        })

    return JSONResponse(content=result)


# ── GET /{filename} — Streaming com HTTP Range ──────────────────────

def _guess_media_type(filename: str) -> str:
    """Infere Content-Type pela extensão do arquivo."""
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mapping = {
        "mp4": "video/mp4",
        "mkv": "video/x-matroska",
        "avi": "video/x-msvideo",
        "mov": "video/quicktime",
        "webm": "video/webm",
        "ts": "video/mp2t",
    }
    return mapping.get(ext, "application/octet-stream")


@app.api_route("/{filename:path}", methods=["GET", "HEAD"])
async def stream_file(filename: str, request: Request):
    """
    Endpoint principal de streaming.

    Sempre responde com 206 Partial Content quando Range é fornecido.
    Sem Range, responde com 200 e o arquivo inteiro.
    """
    movie = await telegram_api.fetch_movie_by_name(filename)
    if not movie:
        return Response(status_code=404, content="Arquivo não encontrado")

    parts = movie["parts"]
    total_size = calculate_total_size(parts)

    if total_size == 0:
        return Response(status_code=404, content="Arquivo sem conteúdo")

    content_type = _guess_media_type(filename)

    # ── HEAD: retorna apenas metadados (Rclone precisa disso) ──
    if request.method == "HEAD":
        logger.info("🔍 HEAD request: %s (%d bytes)", filename, total_size)
        return Response(
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(total_size),
                "Content-Type": content_type,
            }
        )

    range_header = request.headers.get("range")

    # ── Sem Range: retorna o arquivo inteiro ──
    if not range_header:
        logger.info("📡 Full request: %s (%d bytes)", filename, total_size)
        return StreamingResponse(
            stream_file_range(parts, 0, total_size - 1, telegram_api.bot),
            status_code=200,
            media_type=content_type,
            headers={
                "Content-Length": str(total_size),
                "Accept-Ranges": "bytes",
            },
        )

    # ── Com Range: 206 Partial Content ──
    try:
        byte_start, byte_end = parse_range_header(range_header, total_size)
    except ValueError:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total_size}"},
        )

    if byte_start >= total_size or byte_start > byte_end:
        return Response(
            status_code=416,
            headers={"Content-Range": f"bytes */{total_size}"},
        )

    content_length = byte_end - byte_start + 1

    logger.info(
        "📡 Range request: %s  bytes=%d-%d  (%.2f MB de %.2f MB)",
        filename,
        byte_start,
        byte_end,
        content_length / (1024 * 1024),
        total_size / (1024 * 1024),
    )

    return StreamingResponse(
        stream_file_range(parts, byte_start, byte_end, telegram_api.bot),
        status_code=206,
        media_type=content_type,
        headers={
            "Content-Range": f"bytes {byte_start}-{byte_end}/{total_size}",
            "Content-Length": str(content_length),
            "Accept-Ranges": "bytes",
        },
    )
