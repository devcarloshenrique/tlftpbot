"""
telegram_api.py — Camada de conexão com o Telegram via Pyrogram.

Responsabilidades:
    1. Inicializar e manter um Pyrogram Client (bot).
    2. Conectar ao MongoDB para ler metadados dos arquivos.
    3. Fazer streaming de chunks do Telegram usando stream_media.
    4. Fornecer funções para listar filmes disponíveis.
"""

from __future__ import annotations

import logging
from os import environ
from typing import AsyncIterator, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client
from pyrogram.errors import FloodWait

import asyncio

logger = logging.getLogger("TLStream")

# ──────────────────────────────────────────────────────────────────────
# Singletons globais — inicializados na startup do FastAPI (lifespan)
# ──────────────────────────────────────────────────────────────────────
bot: Optional[Client] = None
db = None  # motor database reference
CHAT_ID: Optional[int] = None  # ID do canal onde os chunks estão armazenados


async def init_bot() -> Client:
    """
    Cria e inicia o Pyrogram Client usando variáveis de ambiente.
    Deve ser chamada UMA VEZ durante o startup da aplicação.
    """
    global bot, CHAT_ID
    api_id = int(environ["API_ID"])
    api_hash = environ["API_HASH"]
    bot_token = environ["BOT_TOKEN"]

    raw_chat = environ.get("CHAT_ID", "")
    if raw_chat:
        CHAT_ID = int(raw_chat)

    bot = Client(
        "tl_stream_bot",
        api_id=api_id,
        api_hash=api_hash,
        bot_token=bot_token,
        workdir="sessions",
    )

    logger.info("🤖 Iniciando Pyrogram Client...")
    await bot.start()
    logger.info("✅ Pyrogram Client conectado.")

    # ── Pre-cache do Peer para evitar "Peer id invalid" ──
    if CHAT_ID is not None:
        try:
            chat_info = await bot.get_chat(CHAT_ID)
            logger.info(
                "✅ Peer cacheado com sucesso: %s (%d)",
                chat_info.title,
                chat_info.id,
            )
        except Exception as e:
            logger.warning("⚠️ Não foi possível fazer pre-cache do chat: %s", e)

    return bot


async def stop_bot():
    """Para o Pyrogram Client de forma limpa."""
    global bot
    if bot:
        await bot.stop()
        logger.info("👋 Pyrogram Client desconectado.")
        bot = None


async def init_db():
    """
    Inicializa a conexão com o MongoDB (mesmo banco usado pelo NebulaFTP).
    """
    global db
    mongo_uri = environ.get("MONGODB", "mongodb://mongo:27017")
    client = AsyncIOMotorClient(mongo_uri)
    db = client.ftp  # Mesmo database do NebulaFTP
    logger.info("✅ MongoDB conectado: %s", mongo_uri)


# ──────────────────────────────────────────────────────────────────────
# Funções de dados
# ──────────────────────────────────────────────────────────────────────

async def fetch_movies() -> list[dict]:
    """
    Busca todos os filmes (arquivos com status 'completed' e parts não-vazios)
    do MongoDB.

    Returns:
        Lista de dicts com:
            - name: str
            - size: int
            - parts: list[dict]   (cada dict tem tg_file, tg_message, file_size, part_id)
    """
    if db is None:
        raise RuntimeError("DB não inicializado. Chame init_db() primeiro.")

    cursor = db.files.find({
        "type": "file",
        "status": "completed",
        "parts": {"$exists": True, "$ne": []},
    })

    movies = []
    async for doc in cursor:
        movies.append({
            "name": doc["name"],
            "size": doc.get("size", 0),
            "parent": doc.get("parent", "/"),
            "parts": sorted(doc.get("parts", []), key=lambda p: p["part_id"]),
        })

    return movies


async def fetch_movie_by_name(filename: str) -> Optional[dict]:
    """
    Busca um filme específico pelo nome.

    Returns:
        Dict com name, size, parts ou None se não encontrado.
    """
    if db is None:
        raise RuntimeError("DB não inicializado.")

    doc = await db.files.find_one({
        "type": "file",
        "name": filename,
        "status": "completed",
        "parts": {"$exists": True, "$ne": []},
    })

    if not doc:
        return None

    return {
        "name": doc["name"],
        "size": doc.get("size", 0),
        "parent": doc.get("parent", "/"),
        "parts": sorted(doc.get("parts", []), key=lambda p: p["part_id"]),
    }


# ──────────────────────────────────────────────────────────────────────
# Streaming de chunks do Telegram (via stream_media)
# ──────────────────────────────────────────────────────────────────────

MAX_STREAM_RETRIES = 5


async def stream_chunk(
    client: Client,
    message_id: int,
    offset: int = 0,
    limit: int = 0,
) -> AsyncIterator[bytes]:
    """
    Gerador assíncrono que faz streaming de um chunk do Telegram
    usando client.stream_media(), descartando bytes iniciais até atingir
    o offset solicitado.

    Args:
        client:     Instância do Pyrogram Client.
        message_id: ID da mensagem no canal do Telegram.
        offset:     Bytes a pular no início do chunk.
        limit:      Máximo de bytes a retornar (0 = sem limite, retorna tudo após offset).

    Yields:
        Blocos de bytes do Telegram stream, já com offset aplicado.
    """
    if CHAT_ID is None:
        raise RuntimeError("CHAT_ID não configurado. Verifique o .env.")

    skipped = 0
    served = 0
    first_yield = True

    for attempt in range(1, MAX_STREAM_RETRIES + 1):
        try:
            # Passo 1: busca o objeto da mensagem pelo chat_id + message_id
            message = await client.get_messages(
                chat_id=CHAT_ID,
                message_ids=message_id,
            )

            # Passo 2: usa o objeto da mensagem no stream_media
            async for chunk in client.stream_media(message=message):
                chunk_len = len(chunk)

                # ── Fase de descarte: pular bytes até atingir o offset ──
                if skipped < offset:
                    remaining_to_skip = offset - skipped
                    if chunk_len <= remaining_to_skip:
                        # Descarta chunk inteiro
                        skipped += chunk_len
                        continue
                    else:
                        # Descarta parcial, começa yield do restante
                        chunk = chunk[remaining_to_skip:]
                        skipped = offset

                # ── Fase de yield ──
                if limit > 0:
                    can_send = limit - served
                    if can_send <= 0:
                        return
                    if len(chunk) > can_send:
                        chunk = chunk[:can_send]

                if first_yield:
                    logger.info(
                        "🎬 Primeiro yield: msg_id=%d, offset=%d, chunk_size=%d bytes",
                        message_id,
                        offset,
                        len(chunk),
                    )
                    first_yield = False

                served += len(chunk)
                yield chunk

                if limit > 0 and served >= limit:
                    return

            # Stream completou sem erro → sai do retry loop
            return

        except FloodWait as e:
            wait = e.value + 2
            logger.warning(
                "⏳ FloodWait no stream (tentativa %d/%d): aguardando %ds",
                attempt,
                MAX_STREAM_RETRIES,
                wait,
            )
            await asyncio.sleep(wait)
            # Reset counters para retry
            skipped = 0
            served = 0
            first_yield = True

        except Exception as e:
            backoff = 2 ** attempt
            logger.error(
                "❌ Erro stream chunk msg_id=%d (tentativa %d/%d): %s",
                message_id,
                attempt,
                MAX_STREAM_RETRIES,
                e,
            )
            if attempt == MAX_STREAM_RETRIES:
                raise
            await asyncio.sleep(backoff)
            skipped = 0
            served = 0
            first_yield = True
