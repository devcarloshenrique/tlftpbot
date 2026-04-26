"""
stream_utils.py — Lógica matemática de conversão de HTTP Range -> Telegram chunks.

Cada chunk do Telegram tem tamanho variável (registrado em file_size de cada part).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import AsyncIterator, List

from app.telegram_api import stream_chunk

logger = logging.getLogger("TLStream")


@dataclass
class ChunkSlice:
    """Representa um pedaço de um chunk que precisa ser servido."""
    chunk_index: int        # Índice do chunk na lista de parts
    offset_start: int       # Offset DENTRO do chunk (bytes a pular no início)
    bytes_to_read: int       # Quantos bytes ler deste chunk


def calculate_total_size(parts: List[dict]) -> int:
    """
    Calcula o tamanho total do arquivo somando o file_size de cada parte.
    """
    return sum(p.get("file_size", 0) for p in parts)


def calculate_chunk_slices(
    byte_start: int,
    byte_end: int,
    parts: List[dict],
) -> list[ChunkSlice]:
    """
    Converte um range HTTP (byte_start, byte_end inclusive) em uma lista
    de ChunkSlice indicando quais chunks buscar e com quais offsets.
    """
    slices: list[ChunkSlice] = []
    cumulative = 0

    for idx, part in enumerate(parts):
        part_size = part.get("file_size", 0)
        part_start = cumulative
        part_end = cumulative + part_size - 1

        if part_end < byte_start:
            cumulative += part_size
            continue

        if part_start > byte_end:
            break

        local_start = max(0, byte_start - part_start)
        local_end = min(part_size - 1, byte_end - part_start)
        bytes_to_read = local_end - local_start + 1

        slices.append(ChunkSlice(
            chunk_index=idx,
            offset_start=local_start,
            bytes_to_read=bytes_to_read,
        ))

        cumulative += part_size

    return slices


def parse_range_header(range_header: str, total_size: int) -> tuple[int, int]:
    """
    Interpreta o cabeçalho Range do HTTP.

    Suporta formatos:
        bytes=0-499         -> (0, 499)
        bytes=500-          -> (500, total_size - 1)
        bytes=-500          -> (total_size - 500, total_size - 1)
    """
    range_header = range_header.strip()
    if not range_header.startswith("bytes="):
        raise ValueError(f"Range header inválido: {range_header}")

    range_spec = range_header[len("bytes="):]
    parts = range_spec.split("-", 1)

    if parts[0] == "":
        suffix_length = int(parts[1])
        byte_start = max(0, total_size - suffix_length)
        byte_end = total_size - 1
    elif parts[1] == "":
        byte_start = int(parts[0])
        byte_end = total_size - 1
    else:
        byte_start = int(parts[0])
        byte_end = int(parts[1])

    byte_start = max(0, byte_start)
    byte_end = min(byte_end, total_size - 1)

    return byte_start, byte_end


async def stream_file_range(
    parts: List[dict],
    byte_start: int,
    byte_end: int,
    bot,
) -> AsyncIterator[bytes]:
    """
    Gerador assíncrono que faz streaming dos chunks necessários do Telegram
    e faz yield dos bytes no range (byte_start..byte_end) solicitado.

    Usa stream_chunk (stream_media do Pyrogram) em vez de carregar chunks
    inteiros na memória.
    """
    slices = calculate_chunk_slices(byte_start, byte_end, parts)

    for i, chunk_slice in enumerate(slices):
        part = parts[chunk_slice.chunk_index]
        message_id = part["tg_message"]

        logger.info(
            "📥 Streaming chunk #%d (msg_id=%d), offset=%d, bytes_to_read=%d  [slice %d/%d]",
            chunk_slice.chunk_index,
            message_id,
            chunk_slice.offset_start,
            chunk_slice.bytes_to_read,
            i + 1,
            len(slices),
        )

        async for data in stream_chunk(
            client=bot,
            message_id=message_id,
            offset=chunk_slice.offset_start,
            limit=chunk_slice.bytes_to_read,
        ):
            yield data
