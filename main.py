import asyncio
import io
import logging
import os
import signal
import time
import uuid
from logging.handlers import RotatingFileHandler
from os import environ
from os.path import exists
from types import SimpleNamespace

import aiofiles
import requests
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError

# Imports locais
from ftp import Server, MongoDBPathIO, MongoDBUserManager
from ftp.common import UPLOAD_QUEUE

if exists(".env"):
    from dotenv import load_dotenv

    load_dotenv()

# --- CARREGAMENTO DE CONFIGURACOES DO .ENV ---
LOG_LEVEL = environ.get("LOG_LEVEL", "INFO")
CHUNK_SIZE_MB = int(environ.get("CHUNK_SIZE_MB", 64))
CHUNK_SIZE = CHUNK_SIZE_MB * 1024 * 1024
MAX_RETRIES = int(environ.get("MAX_RETRIES", 5))
MAX_STAGING_AGE = int(environ.get("MAX_STAGING_AGE", 3600))
MAX_WORKERS = int(environ.get("MAX_WORKERS", 4))
SESSION_WORKDIR = environ.get("PYROGRAM_WORKDIR", "sessions")
SESSION_NAME = environ.get("PYROGRAM_SESSION_NAME", "Nebula_MonoBot")
STARTUP_FLOODWAIT_GRACE = int(environ.get("STARTUP_FLOODWAIT_GRACE", 5))

# Portas Passivas
PASSIVE_PORTS = None
pp_str = environ.get("PASSIVE_PORTS")
if pp_str and "-" in pp_str:
    try:
        start_p, end_p = map(int, pp_str.split("-"))
        PASSIVE_PORTS = range(start_p, end_p + 1)
    except Exception:
        pass

# --- CONTROLE DE LOCKS (PROTECAO) ---
# Conjunto para armazenar caminhos de arquivos que estao sendo enviados agora.
# O Garbage Collector NAO pode tocar nestes arquivos.
ACTIVE_UPLOADS = set()

# --- LOGGING ---
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
log_handler = RotatingFileHandler(
    "nebula.log", maxBytes=5 * 1024 * 1024, backupCount=2
)
log_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
logger = logging.getLogger("NebulaFTP")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
if not logger.handlers:
    logger.addHandler(log_handler)
    logger.addHandler(console_handler)


# --- METRICAS ---
class Metrics:
    uploads_total = 0
    uploads_failed = 0
    bytes_uploaded = 0

    @classmethod
    def log_success(cls, size):
        cls.uploads_total += 1
        cls.bytes_uploaded += size

    @classmethod
    def log_fail(cls):
        cls.uploads_failed += 1

    @classmethod
    def report(cls):
        mb = cls.bytes_uploaded / (1024 * 1024)
        logger.info(
            f"📊 Stats: ⬆️ {cls.uploads_total} uploads ({mb:.2f} MB) | ❌ {cls.uploads_failed} falhas"
        )


async def stats_reporter():
    while True:
        await asyncio.sleep(300)
        Metrics.report()


async def setup_database_indexes(mongo):
    logger.info("🔧 Verificando indices do Banco de Dados...")
    try:
        await mongo.files.create_index([("parent", 1), ("name", 1)], unique=True)
        await mongo.files.create_index("parent")
        await mongo.files.create_index("uploadId", sparse=True)
        await mongo.files.create_index("uploaded_at")
        await mongo.files.create_index("status")
        logger.info("✅ Indices verificados.")
    except Exception as e:
        logger.warning(f"⚠️ Aviso indices: {e}")


async def garbage_collector():
    logger.info(f"🧹 Garbage Collector Iniciado (Max Age: {MAX_STAGING_AGE}s)")
    staging_dir = "staging"
    while True:
        try:
            now = time.time()
            if os.path.exists(staging_dir):
                for root, dirs, files in os.walk(staging_dir):
                    for f in files:
                        if f.endswith(".partial"):
                            continue
                        fp = os.path.join(root, f)

                        # --- PROTECAO CRITICA ---
                        # Se o arquivo estiver sendo enviado, PULA.
                        if fp in ACTIVE_UPLOADS:
                            continue
                        # ------------------------

                        if now - os.path.getmtime(fp) > MAX_STAGING_AGE:
                            try:
                                os.remove(fp)
                                logger.warning(f"🧹 GC: Lixo removido: {f}")
                            except Exception as e:
                                logger.error(f"❌ GC Erro {f}: {e}")
        except Exception as e:
            logger.error(f"❌ GC Falha Geral: {e}")
        await asyncio.sleep(600)


async def folder_watcher(mongo):
    """
    Vigia a pasta 'staging' RECURSIVAMENTE.
    Mapeia arquivos para a PASTA DO UTILIZADOR.
    """
    logger.info("👀 Folder Watcher Iniciado")
    staging_dir = "staging"
    if not os.path.exists(staging_dir):
        os.makedirs(staging_dir)

    target_root = "/"
    try:
        user = await mongo.users.find_one({})
        if user:
            target_root = f"/{user['login']}"
            logger.info(f"🎯 Modo MonoBot: Arquivos de staging irao para: {target_root}")
        else:
            logger.warning(
                "⚠️ Nenhum utilizador encontrado no DB. Arquivos irao para a Raiz '/'."
            )
    except Exception as e:
        logger.error(f"❌ Erro ao buscar utilizador: {e}")

    while True:
        try:
            for root, dirs, files in os.walk(staging_dir):
                for f in files:
                    if f.endswith(".partial"):
                        continue
                    fp = os.path.join(root, f)

                    if not os.path.isfile(fp):
                        continue

                    # Ignora se ja estiver sendo enviado (evita duplicar na fila)
                    if fp in ACTIVE_UPLOADS:
                        continue

                    size_t1 = os.path.getsize(fp)
                    if size_t1 == 0:
                        continue

                    rel_dir = os.path.relpath(root, staging_dir)

                    if rel_dir == ".":
                        parent_path = target_root
                    else:
                        normalized_rel = rel_dir.replace(os.sep, "/")
                        if target_root == "/":
                            parent_path = f"/{normalized_rel}"
                        else:
                            parent_path = f"{target_root}/{normalized_rel}"

                    doc = await mongo.files.find_one({"name": f, "parent": parent_path})

                    if not doc:
                        await asyncio.sleep(2)
                        if os.path.getsize(fp) != size_t1:
                            continue

                        logger.info(f"👀 Detectado: {f} -> {parent_path}")

                        if parent_path != "/":
                            parts = parent_path.strip("/").split("/")
                            current_parent = "/"
                            for part in parts:
                                await mongo.files.update_one(
                                    {"name": part, "parent": current_parent},
                                    {
                                        "$setOnInsert": {
                                            "type": "dir",
                                            "ctime": int(time.time()),
                                            "mtime": int(time.time()),
                                            "size": 0,
                                        }
                                    },
                                    upsert=True,
                                )
                                if current_parent == "/":
                                    current_parent = "/" + part
                                else:
                                    current_parent = f"{current_parent}/{part}"

                        file_doc = {
                            "type": "file",
                            "name": f,
                            "parent": parent_path,
                            "size": size_t1,
                            "status": "staging",
                            "local_path": fp,
                            "mtime": int(time.time()),
                            "ctime": int(time.time()),
                            "parts": [],
                        }

                        try:
                            await mongo.files.insert_one(file_doc)
                            await UPLOAD_QUEUE.put(
                                {
                                    "path": fp,
                                    "filename": f,
                                    "parent": parent_path,
                                    "size": size_t1,
                                }
                            )
                            logger.info(f"📤 Enfileirado: {f}")
                        except Exception as e:
                            logger.warning(f"⚠️ Erro registro {f}: {e}")

        except Exception as e:
            logger.error(f"❌ Erro Watcher: {e}")

        await asyncio.sleep(5)


def get_bot_token():
    token_str = environ.get("BOT_TOKENS") or environ.get("BOT_TOKEN") or ""
    return token_str.split(",")[0].strip()


async def bot_api_get_chat(chat_id):
    token = get_bot_token()
    if not token:
        raise ValueError("BOT_TOKEN ausente para fallback via Bot API")

    url = f"https://api.telegram.org/bot{token}/getChat"

    def _request():
        return requests.get(url, params={"chat_id": str(chat_id)}, timeout=30)

    response = await asyncio.to_thread(_request)
    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(payload.get("description", "erro desconhecido em getChat"))
    return payload["result"]


async def bot_api_send_message(chat_id, text, disable_notification=True):
    token = get_bot_token()
    if not token:
        raise ValueError("BOT_TOKEN ausente para envio via Bot API")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": str(chat_id), "text": text}
    if disable_notification:
        data["disable_notification"] = "true"

    def _request():
        return requests.post(url, data=data, timeout=30)

    response = await asyncio.to_thread(_request)
    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(payload.get("description", "erro desconhecido em sendMessage"))
    return payload["result"]


async def bot_api_send_document(chat_id, filename, file_bytes):
    token = get_bot_token()
    if not token:
        raise ValueError("BOT_TOKEN ausente para envio de documentos via Bot API")

    url = f"https://api.telegram.org/bot{token}/sendDocument"

    def _request():
        files = {
            "document": (
                filename,
                file_bytes,
                "application/octet-stream",
            )
        }
        data = {"chat_id": str(chat_id), "disable_notification": "true", "caption": ""}
        return requests.post(url, data=data, files=files, timeout=300)

    response = await asyncio.to_thread(_request)
    payload = response.json()
    if not payload.get("ok"):
        raise ValueError(payload.get("description", "erro desconhecido em sendDocument"))

    result = payload["result"]
    file_id = result.get("document", {}).get("file_id")
    message_id = result.get("message_id")

    if not file_id or message_id is None:
        raise ValueError("resposta invalida em sendDocument (file_id/message_id ausentes)")

    return SimpleNamespace(
        id=message_id,
        document=SimpleNamespace(file_id=file_id),
    )


async def send_document_resilient(bot, chat_id, mem_file, chunk_name):
    mem_file.seek(0)
    try:
        return await bot.send_document(
            chat_id=chat_id,
            document=mem_file,
            file_name=chunk_name,
            force_document=True,
            caption="",
        )
    except ValueError as e:
        if "Peer id invalid" not in str(e):
            raise

        logger.warning(
            "⚠️ Peer id invalid no Pyrogram para '%s'. Usando Bot API no upload.",
            chat_id,
        )
        mem_file.seek(0)
        return await bot_api_send_document(chat_id, chunk_name, mem_file.read())


async def upload_worker(bot, target_chat_id, mongo, worker_id):
    logger.info(f"👷 Worker #{worker_id} Pronto")

    while True:
        try:
            task = await asyncio.wait_for(UPLOAD_QUEUE.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue

        local_path = task["path"]
        filename = task["filename"]
        parent = task["parent"]

        # --- LOCK: Bloqueia o arquivo para o GC nao apagar ---
        ACTIVE_UPLOADS.add(local_path)
        # -----------------------------------------------------

        try:
            if filename.endswith(".partial"):
                continue

            if not os.path.exists(local_path):
                continue

            real_size = os.path.getsize(local_path)
            if real_size == 0:
                try:
                    os.remove(local_path)
                except Exception:
                    pass
                continue

            logger.info(
                f"⬆️ [W{worker_id}] Processando: {filename} ({real_size/1024/1024:.2f} MB)"
            )

            file_doc = await mongo.files.find_one({"name": filename, "parent": parent})
            if not file_doc:
                logger.warning(f"⚠️ [W{worker_id}] Metadados nao encontrados: {filename}")
                continue

            file_uuid = str(uuid.uuid4())
            parts_metadata = []
            upload_failed = False

            try:
                async with aiofiles.open(local_path, "rb") as f:
                    part_num = 0
                    while True:
                        chunk_data = await f.read(CHUNK_SIZE)
                        if not chunk_data:
                            break

                        chunk_name = f"{file_uuid}.part_{part_num:03d}"
                        mem_file = io.BytesIO(chunk_data)
                        mem_file.name = chunk_name
                        sent_msg = None

                        for attempt in range(1, MAX_RETRIES + 1):
                            try:
                                sent_msg = await send_document_resilient(
                                    bot,
                                    target_chat_id,
                                    mem_file,
                                    chunk_name,
                                )
                                break
                            except FloodWait as e:
                                wait_seconds = e.value + 2
                                logger.warning(
                                    f"⏳ [W{worker_id}] FloodWait: {wait_seconds}s"
                                )
                                await asyncio.sleep(wait_seconds)
                            except RPCError as e:
                                wait_seconds = 2**attempt
                                logger.error(
                                    f"❌ [W{worker_id}] Erro TG ({attempt}): {e}"
                                )
                                await asyncio.sleep(wait_seconds)
                            except Exception as e:
                                logger.error(f"❌ [W{worker_id}] Erro: {e}")
                                await asyncio.sleep(5)

                        if not sent_msg:
                            raise Exception(f"Falha upload parte {part_num}")

                        parts_metadata.append(
                            {
                                "part_id": part_num,
                                "tg_file": sent_msg.document.file_id,
                                "tg_message": sent_msg.id,
                                "file_size": len(chunk_data),
                                "chunk_name": chunk_name,
                            }
                        )
                        part_num += 1
                        await asyncio.sleep(0.2)

            except Exception as e:
                logger.error(f"❌ [W{worker_id}] Abortado: {filename}: {e}")
                upload_failed = True
                Metrics.log_fail()

            if not upload_failed:
                await mongo.files.update_one(
                    {"_id": file_doc["_id"]},
                    {
                        "$set": {
                            "size": real_size,
                            "uploaded_at": int(time.time()),
                            "parts": parts_metadata,
                            "obfuscated_id": file_uuid,
                            "status": "completed",
                        },
                        "$unset": {"uploadId": 1, "local_path": 1},
                    },
                )
                logger.info(f"✅ [W{worker_id}] Concluido: {filename}")
                Metrics.log_success(real_size)
                # Agora sim o GC ou nos mesmos podemos remover
                try:
                    os.remove(local_path)
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"❌ [W{worker_id}] Critico: {e}")
        finally:
            # --- UNLOCK: Libera o arquivo ---
            ACTIVE_UPLOADS.discard(local_path)
            UPLOAD_QUEUE.task_done()


async def resolve_channel(bot):
    raw_chat = environ.get("CHAT_ID")
    if not raw_chat:
        logger.critical("❌ CHAT_ID nao informado no .env")
        return None

    target_chat = int(raw_chat) if raw_chat.lstrip("-").isdigit() else raw_chat

    logger.info("🔍 Verificando acesso ao canal...")

    try:
        chat = await bot.get_chat(target_chat)
        logger.info(f"✅ Canal Confirmado: {chat.title} (ID: {chat.id})")
        try:
            await bot.send_message(
                chat.id,
                "🔄 Nebula FTP MonoBot Conectado",
                disable_notification=True,
            )
        except Exception:
            pass
        return chat.id
    except Exception as pyrogram_error:
        logger.warning(
            "⚠️ Pyrogram nao resolveu o canal '%s': %s. Tentando Bot API...",
            target_chat,
            pyrogram_error,
        )

    try:
        chat = await bot_api_get_chat(target_chat)
        chat_id = chat.get("id", target_chat)
        title = chat.get("title") or chat.get("username") or "Canal sem titulo"
        logger.info(f"✅ Canal Confirmado via Bot API: {title} (ID: {chat_id})")
        try:
            await bot_api_send_message(
                chat_id,
                "🔄 Nebula FTP MonoBot Conectado",
                disable_notification=True,
            )
        except Exception as notice_error:
            logger.warning(
                f"⚠️ Aviso: nao foi possivel enviar mensagem de conexao: {notice_error}"
            )
        return chat_id
    except Exception as bot_api_error:
        logger.critical(f"❌ Canal invalido '{target_chat}': {bot_api_error}")
        return None


async def start_bot_with_retry(bot):
    while True:
        try:
            await bot.start()
            return True
        except FloodWait as e:
            wait_seconds = int(getattr(e, "value", 0) or 0) + STARTUP_FLOODWAIT_GRACE
            if wait_seconds <= 0:
                wait_seconds = 60
            logger.error(
                "⏳ FloodWait ao iniciar bot. Aguardando %ss antes de tentar novamente...",
                wait_seconds,
            )
            await asyncio.sleep(wait_seconds)
        except Exception as e:
            logger.critical(f"❌ Falha ao iniciar bot: {e}")
            return False


async def main():
    api_id = int(environ.get("API_ID"))
    api_hash = environ.get("API_HASH")
    token = get_bot_token()

    if not token:
        logger.critical("❌ Sem token!")
        return

    os.makedirs(SESSION_WORKDIR, exist_ok=True)
    bot = Client(
        SESSION_NAME,
        api_id=api_id,
        api_hash=api_hash,
        bot_token=token,
        workdir=SESSION_WORKDIR,
    )
    logger.info("🤖 Iniciando Bot...")
    if not await start_bot_with_retry(bot):
        return

    target_chat_id = await resolve_channel(bot)
    if not target_chat_id:
        await bot.stop()
        return

    loop = asyncio.get_event_loop()
    try:
        mongo = AsyncIOMotorClient(environ.get("MONGODB"), io_loop=loop, w="majority").ftp
        await setup_database_indexes(mongo)
    except Exception as e:
        logger.critical(f"❌ Erro DB: {e}")
        return

    MongoDBPathIO.db = mongo
    MongoDBPathIO.tg = bot
    server = Server(MongoDBUserManager(mongo), MongoDBPathIO)

    asyncio.create_task(garbage_collector())
    asyncio.create_task(stats_reporter())
    asyncio.create_task(folder_watcher(mongo))

    for i in range(MAX_WORKERS):
        asyncio.create_task(upload_worker(bot, target_chat_id, mongo, i + 1))

    port = int(environ.get("PORT", 2121))
    logger.info(f"🚀 Nebula FTP (MonoBot) Rodando na porta {port}")

    ftp_server_task = asyncio.create_task(server.run(environ.get("HOST", "0.0.0.0"), port))

    stop_event = asyncio.Event()
    loop.add_signal_handler(signal.SIGINT, stop_event.set)
    loop.add_signal_handler(signal.SIGTERM, stop_event.set)

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("⏳ Shutdown...")
        try:
            if not UPLOAD_QUEUE.empty():
                await asyncio.wait_for(UPLOAD_QUEUE.join(), timeout=30)
        except Exception:
            pass
        await server.close()
        await bot.stop()
        ftp_server_task.cancel()
        logger.info("👋 Desligado.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass