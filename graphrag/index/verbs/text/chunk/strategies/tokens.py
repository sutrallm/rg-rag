# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""A module containing run and split_text_on_tokens methods definition."""

from collections.abc import Iterable
from typing import Any

import tiktoken
from datashaper import ProgressTicker

from graphrag.index.text_splitting import Tokenizer
from graphrag.index.verbs.text.chunk.typing import TextChunk

DEFAULT_CHUNK_SIZE = 2500  # tokens
DEFAULT_CHUNK_OVERLAP = 300  # tokens


def run(
    input: list[str], args: dict[str, Any], tick: ProgressTicker
) -> Iterable[TextChunk]:
    """Chunks text into multiple parts. A pipeline verb."""
    tokens_per_chunk = args.get("chunk_size", DEFAULT_CHUNK_SIZE)
    chunk_overlap = args.get("chunk_overlap", DEFAULT_CHUNK_OVERLAP)
    encoding_name = args.get("encoding_name", "cl100k_base")
    enc = tiktoken.get_encoding(encoding_name)

    def encode(text: str) -> list[int]:
        if not isinstance(text, str):
            text = f"{text}"
        return enc.encode(text)

    def decode(tokens: list[int]) -> str:
        return enc.decode(tokens)

    return split_text_on_tokens(
        input,
        Tokenizer(
            chunk_overlap=chunk_overlap,
            tokens_per_chunk=tokens_per_chunk,
            encode=encode,
            decode=decode,
        ),
        tick,
    )


def split_text_on_tokens(
    texts: list[str], enc: Tokenizer, tick: ProgressTicker
) -> list[TextChunk]:
    """Split incoming text and return chunks."""
    from graphrag.my_graphrag.db import get_chunks_for_graphrag
    result = []
    for source_doc_idx, text in enumerate(texts):
        tick(1)

        chunks = get_chunks_for_graphrag(text)
        for chunk in chunks:
            # no sub chunk
            result.append(
                TextChunk(
                    text_chunk=chunk,
                    source_doc_indices=[source_doc_idx],
                    n_tokens=len(enc.encode(chunk)),
                )
            )

            # add sub chunk
            # encoded = enc.encode(chunk)
            # input_ids: list[tuple[int, int]] = [(source_doc_idx, id) for id in encoded]
            #
            # start_idx = 0
            # cur_idx = min(start_idx + enc.tokens_per_chunk, len(input_ids))
            # chunk_ids = input_ids[start_idx:cur_idx]
            # while start_idx < len(input_ids):
            #     chunk_text = enc.decode([id for _, id in chunk_ids])
            #     result.append(
            #         TextChunk(
            #             text_chunk=chunk_text,
            #             source_doc_indices=[source_doc_idx],
            #             n_tokens=len(chunk_ids),
            #         )
            #     )
            #     start_idx += enc.tokens_per_chunk - enc.chunk_overlap
            #     cur_idx = min(start_idx + enc.tokens_per_chunk, len(input_ids))
            #     chunk_ids = input_ids[start_idx:cur_idx]

    return result
