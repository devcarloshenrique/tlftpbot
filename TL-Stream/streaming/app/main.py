"""
main.py — Ponto de entrada do FastAPI para o TL-Stream.

Endpoints:
    GET  /                 → Directory index raiz (HTML para o Rclone parsear).
    GET  /{path:path}      → Diretório virtual OU streaming de vídeo (HTTP Range 206).
    HEAD /{path:path}      → Metadados de arquivo / diretório.
    GET  /fetch-movies     → JSON com todos os filmes e seus chunks (debug).
    GET  /debug-vfs        → JSON da árvore VFS gerada (debug).
"""

from __future__ import annotations

import logging
import mimetypes
from collections import defaultdict
from contextlib import asynccontextmanager
from os import environ
from os.path import exists
from urllib.parse import quote, unquote

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


# ── Helpers para VFS (Virtual File System) ───────────────────────────

def _normalize(path: str) -> str:
    """Remove barras iniciais/finais e normaliza separadores."""
    return path.strip("/").replace("\\", "/")


def _build_vfs(movies: list[dict]) -> tuple[dict[str, set[str]], dict[str, dict]]:
    """
    Constrói uma árvore de diretórios virtual a partir da lista de filmes.

    O campo 'parent' do MongoDB contém o diretório pai absoluto, e.g.:
        /casaos/streaming
        /casaos/streaming/filmes
        /casaos/streaming/series/breaking-bad

    Se ALLOWED_FOLDER estiver definido (e.g. "casaos/streaming"), o prefixo
    correspondente é removido para que as pastas apareçam a partir da raiz
    do ponto de montagem Rclone.

    Returns:
        (dir_tree, file_map)
        - dir_tree: { "dir_path_normalizado" : set("filho1", "filho2/") }
          Diretórios filhos têm sufixo '/', arquivos não.
        - file_map: { "rel/path/arquivo.mkv" : movie_dict }
    """
    raw_allowed = environ.get("ALLOWED_FOLDER", "")
    allowed = _normalize(raw_allowed)

    dir_tree: dict[str, set[str]] = defaultdict(set)
    file_map: dict[str, dict] = {}

    # Raiz sempre existe
    dir_tree[""] = dir_tree.get("", set())

    for movie in movies:
        parent_raw = _normalize(movie.get("parent", ""))
        filename = movie["name"]

        # ── Calcula rel_parent (caminho relativo ao ALLOWED_FOLDER) ──
        if allowed:
            if parent_raw == allowed:
                rel_parent = ""
            elif parent_raw.startswith(allowed + "/"):
                rel_parent = parent_raw[len(allowed) + 1:]
            else:
                # Se não começa com allowed, pula (não deveria acontecer
                # dado o filtro no fetch_movies, mas proteção extra)
                rel_parent = parent_raw
        else:
            rel_parent = parent_raw

        # ── Caminho completo do arquivo relativo à raiz do mount ──
        full_path = f"{rel_parent}/{filename}" if rel_parent else filename

        # Registra o arquivo
        file_map[full_path] = movie

        # Registra o arquivo como filho do seu diretório pai
        dir_tree[rel_parent].add(filename)

        # ── Registra toda a cadeia de diretórios intermediários ──
        if rel_parent:
            segments = rel_parent.split("/")
            for depth in range(len(segments)):
                parent_dir = "/".join(segments[:depth])      # "" para depth=0
                child_dir_name = segments[depth] + "/"       # trailing slash = diretório
                dir_tree[parent_dir].add(child_dir_name)

                # Garante que o próprio diretório atual exista como chave
                current_dir = "/".join(segments[: depth + 1])
                if current_dir not in dir_tree:
                    dir_tree[current_dir] = set()

    return dict(dir_tree), file_map


def _render_directory_html(dir_path: str, children: set[str]) -> str:
    """
    Gera HTML de directory index no formato Apache/Nginx que o Rclone parseia.
    Diretórios têm trailing slash no href; arquivos não.
    """
    display_path = f"/{dir_path}/" if dir_path else "/"
    lines = []

    # Ordena: diretórios primeiro (têm '/'), depois arquivos
    sorted_children = sorted(
        children,
        key=lambda x: (not x.endswith("/"), x.lower()),
    )

    for child in sorted_children:
        encoded = quote(child)
        # Tamanho: '-' para dirs, placeholder para arquivos
        size_str = "-"
        lines.append(
            f'<a href="{encoded}">{child}</a>'
            f"             01-Jan-2026 00:00  {size_str}"
        )

    entries = "\n".join(lines)

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        f"<head><title>Index of {display_path}</title></head>\n"
        "<body>\n"
        f"<h1>Index of {display_path}</h1><hr><pre>\n"
        f"{entries}\n"
        "</pre><hr>\n"
        "</body>\n"
        "</html>"
    )


# ── GET /fetch-movies — JSON de filmes e chunks (debug) ─────────────

@app.get("/fetch-movies")
async def fetch_movies_endpoint():
    movies = await telegram_api.fetch_movies()
    return JSONResponse(content=[
        {
            "name": m["name"],
            "size": m["size"],
            "parent": m["parent"],
            "chunks": [
                {"part_id": p["part_id"], "tg_message": p["tg_message"], "file_size": p["file_size"]}
                for p in m["parts"]
            ],
        }
        for m in movies
    ])


# ── GET /debug-vfs — Debug da árvore virtual ─────────────────────────

@app.get("/debug-vfs")
async def debug_vfs_endpoint():
    """Retorna a árvore VFS gerada para debug; mostra dirs e file_map."""
    movies = await telegram_api.fetch_movies()
    dir_tree, file_map = _build_vfs(movies)
    return JSONResponse(content={
        "allowed_folder": environ.get("ALLOWED_FOLDER", ""),
        "total_movies": len(movies),
        "raw_parents": list({m["parent"] for m in movies}),
        "dir_tree": {k: sorted(v) for k, v in dir_tree.items()},
        "file_paths": sorted(file_map.keys()),
    })


# ── Helpers de streaming ─────────────────────────────────────────────

def _guess_media_type(filename: str) -> str:
    """Infere Content-Type pela extensão do arquivo."""
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        return mime
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "mp4": "video/mp4",
        "mkv": "video/x-matroska",
        "avi": "video/x-msvideo",
        "mov": "video/quicktime",
        "webm": "video/webm",
        "ts": "video/mp2t",
    }.get(ext, "application/octet-stream")


# ── Rota principal — diretório virtual OU streaming de arquivo ───────

@app.api_route("/{req_path:path}", methods=["GET", "HEAD"])
async def handle_path(req_path: str, request: Request):
    """
    Roteador principal que decide entre:
      - Servir um directory index HTML (para o Rclone navegar pastas)
      - Fazer streaming de um arquivo de vídeo (com suporte a HTTP Range)
    """
    # Decodifica e normaliza o path (remove barras extras)
    req_path = _normalize(unquote(req_path))

    logger.info("📂 Request: method=%s  path='%s'", request.method, req_path)

    # Busca todos os filmes e constrói a árvore virtual
    movies = await telegram_api.fetch_movies()
    dir_tree, file_map = _build_vfs(movies)

    # ── Caso 1: É um arquivo? ──
    if req_path in file_map:
        logger.info("🎬 Arquivo encontrado: '%s'", req_path)
        return await _stream_file(file_map[req_path], req_path, request)

    # ── Caso 2: É um diretório? ──
    if req_path in dir_tree:
        children = dir_tree[req_path]
        logger.info(
            "📁 Diretório encontrado: '%s'  (%d filhos: %s)",
            req_path or "/",
            len(children),
            ", ".join(sorted(children)[:10]),
        )

        if request.method == "HEAD":
            return Response(
                headers={"Content-Type": "text/html; charset=utf-8"},
            )

        html = _render_directory_html(req_path, children)
        return HTMLResponse(content=html)

    # ── Caso 3: Não encontrado ──
    logger.warning("❌ Not found: '%s'", req_path)
    return Response(status_code=404, content="Não encontrado")


# ── Streaming de arquivo individual ──────────────────────────────────

async def _stream_file(movie: dict, full_path: str, request: Request):
    """
    Lida com streaming de um arquivo individual, com suporte a
    HEAD requests e HTTP Range (206 Partial Content).
    """
    parts = movie["parts"]
    total_size = calculate_total_size(parts)

    if total_size == 0:
        return Response(status_code=404, content="Arquivo sem conteúdo")

    filename = movie["name"]
    content_type = _guess_media_type(filename)

    # ── HEAD: retorna apenas metadados (Rclone precisa disso) ──
    if request.method == "HEAD":
        logger.info("🔍 HEAD request: %s (%d bytes)", full_path, total_size)
        return Response(
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(total_size),
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=3600",
            }
        )

    range_header = request.headers.get("range")

    # ── Sem Range: retorna o arquivo inteiro ──
    if not range_header:
        logger.info("📡 Full request: %s (%d bytes)", full_path, total_size)
        return StreamingResponse(
            stream_file_range(parts, 0, total_size - 1, telegram_api.bot),
            status_code=200,
            media_type=content_type,
            headers={
                "Content-Length": str(total_size),
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
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
        full_path,
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
            "Cache-Control": "public, max-age=3600",
        },
    )
