# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""A module containing 'GraphExtractionResult' and 'GraphExtractor' models."""

import logging
import numbers
import re
import traceback
import os
import random
from datetime import datetime
from pathlib import Path
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import networkx as nx
import tiktoken

import graphrag.config.defaults as defs
from graphrag.index.typing import ErrorHandlerFn
from graphrag.index.utils import clean_str
from graphrag.llm import CompletionLLM

from .prompts import CONTINUE_PROMPT, GRAPH_EXTRACTION_PROMPT, LOOP_PROMPT

import xml.etree.ElementTree as ET

from graphrag.my_graphrag.db import save_new_relationship

DEFAULT_TUPLE_DELIMITER = "<|>"
DEFAULT_RECORD_DELIMITER = "##"
DEFAULT_COMPLETION_DELIMITER = "<|COMPLETE|>"
DEFAULT_ENTITY_TYPES = ["organization", "person", "geo", "event"]


@dataclass
class GraphExtractionResult:
    """Unipartite graph extraction result class definition."""

    output: nx.Graph
    source_docs: dict[Any, Any]


class GraphExtractor:
    """Unipartite graph extractor class definition."""

    _llm: CompletionLLM
    _join_descriptions: bool
    _tuple_delimiter_key: str
    _record_delimiter_key: str
    _entity_types_key: str
    _input_text_key: str
    _completion_delimiter_key: str
    _entity_name_key: str
    _input_descriptions_key: str
    _extraction_prompt: str
    _summarization_prompt: str
    _loop_args: dict[str, Any]
    _max_gleanings: int
    _on_error: ErrorHandlerFn

    def __init__(
        self,
        llm_invoker: CompletionLLM,
        tuple_delimiter_key: str | None = None,
        record_delimiter_key: str | None = None,
        input_text_key: str | None = None,
        entity_types_key: str | None = None,
        completion_delimiter_key: str | None = None,
        prompt: str | None = None,
        join_descriptions=True,
        encoding_model: str | None = None,
        max_gleanings: int | None = None,
        on_error: ErrorHandlerFn | None = None,
    ):
        """Init method definition."""
        # TODO: streamline construction
        self._llm = llm_invoker
        self._join_descriptions = join_descriptions
        self._input_text_key = input_text_key or "input_text"
        self._tuple_delimiter_key = tuple_delimiter_key or "tuple_delimiter"
        self._record_delimiter_key = record_delimiter_key or "record_delimiter"
        self._completion_delimiter_key = (
            completion_delimiter_key or "completion_delimiter"
        )
        self._entity_types_key = entity_types_key or "entity_types"
        # 240805 define the prompts on prompts.py
        # self._extraction_prompt = prompt or GRAPH_EXTRACTION_PROMPT
        self._extraction_prompt = GRAPH_EXTRACTION_PROMPT
        self._max_gleanings = (
            max_gleanings
            if max_gleanings is not None
            else defs.ENTITY_EXTRACTION_MAX_GLEANINGS
        )
        self._on_error = on_error or (lambda _e, _s, _d: None)

        # Construct the looping arguments
        encoding = tiktoken.get_encoding(encoding_model or "cl100k_base")
        yes = encoding.encode("YES")
        no = encoding.encode("NO")
        self._loop_args = {"logit_bias": {yes[0]: 100, no[0]: 100}, "max_tokens": 1}

    async def __call__(
        self, texts: list[str], prompt_variables: dict[str, Any] | None = None
    ) -> GraphExtractionResult:
        """Call method definition."""
        if prompt_variables is None:
            prompt_variables = {}
        all_records: dict[int, str] = {}
        source_doc_map: dict[int, str] = {}

        # Wire defaults into the prompt variables
        prompt_variables = {
            **prompt_variables,
            self._tuple_delimiter_key: prompt_variables.get(self._tuple_delimiter_key)
            or DEFAULT_TUPLE_DELIMITER,
            self._record_delimiter_key: prompt_variables.get(self._record_delimiter_key)
            or DEFAULT_RECORD_DELIMITER,
            self._completion_delimiter_key: prompt_variables.get(
                self._completion_delimiter_key
            )
            or DEFAULT_COMPLETION_DELIMITER,
            self._entity_types_key: ",".join(
                prompt_variables[self._entity_types_key] or DEFAULT_ENTITY_TYPES
            ),
        }

        for doc_index, text in enumerate(texts):
            try:
                # Invoke the entity extraction
                result = await self._process_document(text, prompt_variables)
                source_doc_map[doc_index] = text
                all_records[doc_index] = result
            except Exception as e:
                logging.exception("error extracting graph")
                self._on_error(
                    e,
                    traceback.format_exc(),
                    {
                        "doc_index": doc_index,
                        "text": text,
                    },
                )

        output = await self._process_results(
            all_records,
            prompt_variables.get(self._tuple_delimiter_key, DEFAULT_TUPLE_DELIMITER),
            prompt_variables.get(self._record_delimiter_key, DEFAULT_RECORD_DELIMITER),
        )

        return GraphExtractionResult(
            output=output,
            source_docs=source_doc_map,
        )

    async def _process_document(
        self, text: str, prompt_variables: dict[str, str]
    ) -> str:
        response = await self._llm(
            self._extraction_prompt,
            variables={
                # 240805 only need input_text for new prompt
                # **prompt_variables,
                self._input_text_key: text,
            },
        )
        results = response.output or ""

        try:
            cur_file_path = Path(os.path.realpath(__file__))
            tmp_prompt_dir = os.path.join(cur_file_path.parents[5], 'prompts', 'tmp')
            if os.path.isdir(tmp_prompt_dir):
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                random_id = random.randint(100000, 999999)
                prefix = f'index_prompt1_{timestamp}_{random_id}_'

                with open(os.path.join(tmp_prompt_dir, f'{prefix}input.txt'), 'w') as f:
                    f.write(self._extraction_prompt.format(input_text=text))
                    f.flush()

                with open(os.path.join(tmp_prompt_dir, f'{prefix}output.txt'), 'w') as f:
                    f.write(results)
                    f.flush()

        except:
            pass

        # Repeat to ensure we maximize entity count
        for i in range(self._max_gleanings):
            # 240829 gleaning
            # response = await self._llm(
            #     CONTINUE_PROMPT,
            #     name=f"extract-continuation-{i}",
            #     history=response.history,
            # )
            gleaning_prompt = self._extraction_prompt + '\n\n' + results + '\n\n' + CONTINUE_PROMPT
            response = await self._llm(
                gleaning_prompt,
                name=f"extract-continuation-{i}",
                variables={
                    self._input_text_key: text,
                },
            )
            results += response.output or ""

            # if this is the final glean, don't bother updating the continuation flag
            if i >= self._max_gleanings - 1:
                break

            # 240829 gleaning
            # response = await self._llm(
            #     LOOP_PROMPT,
            #     name=f"extract-loopcheck-{i}",
            #     history=response.history,
            #     model_parameters=self._loop_args,
            # )
            loop_prompt = self._extraction_prompt + '\n\n' + results + '\n\n' + LOOP_PROMPT
            response = await self._llm(
                loop_prompt,
                name=f"extract-loopcheck-{i}",
                model_parameters=self._loop_args,
                variables={
                    self._input_text_key: text,
                },
            )
            if response.output != "YES":
                break

        # 240805
        conv_output = await self._convert_output(
            results,
            prompt_variables.get(self._tuple_delimiter_key, DEFAULT_TUPLE_DELIMITER),
            prompt_variables.get(self._record_delimiter_key, DEFAULT_RECORD_DELIMITER),
            prompt_variables.get(self._completion_delimiter_key, DEFAULT_COMPLETION_DELIMITER),
            text
        )

        results = conv_output

        return results

    async def _process_results(
        self,
        results: dict[int, str],
        tuple_delimiter: str,
        record_delimiter: str,
    ) -> nx.Graph:
        """Parse the result string to create an undirected unipartite graph.

        Args:
            - results - dict of results from the extraction chain
            - tuple_delimiter - delimiter between tuples in an output record, default is '<|>'
            - record_delimiter - delimiter between records, default is '##'
        Returns:
            - output - unipartite graph in graphML format
        """
        graph = nx.Graph()
        for source_doc_id, extracted_data in results.items():
            records = [r.strip() for r in extracted_data.split(record_delimiter)]

            for record in records:
                record = re.sub(r"^\(|\)$", "", record.strip())
                record_attributes = record.split(tuple_delimiter)

                if record_attributes[0] == '"entity"' and len(record_attributes) >= 4:
                    # add this record as a node in the G
                    entity_name = clean_str(record_attributes[1].upper())
                    entity_type = clean_str(record_attributes[2].upper())
                    entity_description = clean_str(record_attributes[3])

                    if entity_name in graph.nodes():
                        node = graph.nodes[entity_name]
                        if self._join_descriptions:
                            node["description"] = "\n".join(
                                list({
                                    *_unpack_descriptions(node),
                                    entity_description,
                                })
                            )
                        else:
                            if len(entity_description) > len(node["description"]):
                                node["description"] = entity_description
                        node["source_id"] = ", ".join(
                            list({
                                *_unpack_source_ids(node),
                                str(source_doc_id),
                            })
                        )
                        node["entity_type"] = (
                            entity_type if entity_type != "" else node["entity_type"]
                        )
                    else:
                        graph.add_node(
                            entity_name,
                            type=entity_type,
                            description=entity_description,
                            source_id=str(source_doc_id),
                        )

                if (
                    record_attributes[0] == '"relationship"'
                    and len(record_attributes) >= 5
                ):
                    # add this record as edge
                    source = clean_str(record_attributes[1].upper())
                    target = clean_str(record_attributes[2].upper())
                    edge_description = clean_str(record_attributes[3])
                    edge_source_id = clean_str(str(source_doc_id))
                    weight = (
                        float(record_attributes[-1])
                        if isinstance(record_attributes[-1], numbers.Number)
                        else 1.0
                    )
                    if source not in graph.nodes():
                        graph.add_node(
                            source,
                            type="",
                            description="",
                            source_id=edge_source_id,
                        )
                    if target not in graph.nodes():
                        graph.add_node(
                            target,
                            type="",
                            description="",
                            source_id=edge_source_id,
                        )
                    if graph.has_edge(source, target):
                        edge_data = graph.get_edge_data(source, target)
                        if edge_data is not None:
                            weight += edge_data["weight"]
                            if self._join_descriptions:
                                edge_description = "\n".join(
                                    list({
                                        *_unpack_descriptions(edge_data),
                                        edge_description,
                                    })
                                )
                            edge_source_id = ", ".join(
                                list({
                                    *_unpack_source_ids(edge_data),
                                    str(source_doc_id),
                                })
                            )
                    graph.add_edge(
                        source,
                        target,
                        weight=weight,
                        description=edge_description,
                        source_id=edge_source_id,
                    )

        return graph

    # 240805 convert the xml output to original required output
    async def _convert_output(
        self,
        output: str,
        tuple_delimiter: str,
        record_delimiter: str,
        completion_delimiter: str,
        input_chunk: str,
    ) -> str:
        # original input variables: entity_types, tuple_delimiter, record_delimiter, completion_delimiter, input_text
        # new input variables: input_text

        # original output format:
        # ("entity"{tuple_delimiter}<entity_name>{tuple_delimiter}<entity_type>{tuple_delimiter}<entity_description>)
        # {record_delimiter}
        # ("relationship"{tuple_delimiter}<source_entity>{tuple_delimiter}<target_entity>{tuple_delimiter}<relationship_description>{tuple_delimiter}<relationship_strength>)
        # {completion_delimiter}

        # new output format:
        # <entity>
        #     <entity_name>entity_name</entity_name>
        #     <entity_type>entity_type</entity_type>
        #     <entity_description>entity_description</entity_description>
        # </entity>
        # <relationship>
        #     <source_entity>source_entity</source_entity>
        #     <target_entity>target_entity</target_entity>
        #     <relationship_description>relationship_description</relationship_description>
        #     <relationship_strength>relationship_strength</relationship_strength>
        # </relationship>

        if not output:
            return ''

        entity_pattern = re.compile(r'<entity>(.*?)</entity>', re.DOTALL)
        relationship_pattern = re.compile(r'<relationship>(.*?)</relationship>', re.DOTALL)

        entities = entity_pattern.findall(output)
        relationships = relationship_pattern.findall(output)

        original_format = []
        for entity in entities:
            try:
                root = ET.fromstring(f"<root>{entity}</root>")
                name = root.find('entity_name').text
                etype = root.find('entity_type').text
                desc = root.find('entity_description').text
                if name and etype and desc:
                    original_format.append(f'("entity"{tuple_delimiter}{name}{tuple_delimiter}{etype}{tuple_delimiter}{desc})')
            except:
                pass

        for relationship in relationships:
            try:
                root = ET.fromstring(f"<root>{relationship}</root>")
                source = root.find('source_entity').text
                target = root.find('target_entity').text
                desc = root.find('relationship_description').text
                strength = root.find('relationship_strength').text
                if source and target and desc and strength:
                    original_format.append(f'("relationship"{tuple_delimiter}{source}{tuple_delimiter}{target}{tuple_delimiter}{desc}{tuple_delimiter}{strength})')
                    # 240904 save relationship to chromadb
                    save_new_relationship(input_chunk, source, target, desc, strength)
            except:
                pass

        original_str = ('\n' + record_delimiter + '\n').join(original_format) + '\n' + completion_delimiter
        return original_str


def _unpack_descriptions(data: Mapping) -> list[str]:
    value = data.get("description", None)
    return [] if value is None else value.split("\n")


def _unpack_source_ids(data: Mapping) -> list[str]:
    value = data.get("source_id", None)
    return [] if value is None else value.split(", ")

