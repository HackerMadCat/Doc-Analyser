import itertools
import os
import random
from multiprocessing import Value
from multiprocessing.pool import Pool
from typing import List, Any, Iterable

import numpy as np
from contracts.guides.AstBfsGuide import AstBfsGuide
from contracts.guides.AstDfsGuide import AstDfsGuide
from contracts.nodes.Ast import Ast
from contracts.nodes.StringNode import StringNode
from contracts.parser import Parser
from contracts.tokens import tokens
from contracts.tokens.LabelToken import LabelToken
from contracts.tokens.MarkerToken import MarkerToken
from contracts.tokens.PredicateToken import PredicateToken
from contracts.tokens.Token import Token
from contracts.visitors.AstCompiler import AstCompiler
from contracts.visitors.AstEqualReducer import AstEqualReducer
from contracts.visitors.AstVisitor import AstVisitor

from config import init
from constants import embeddings
from constants.analyser import BATCH_SIZE, SEED
from constants.paths import ANALYSER_METHODS, JODA_TIME_DATA_SET
from constants.tags import PARTS, PAD, NOP
from generate_embeddings import join_java_doc, empty
from utils import Filter, Dumper
from utils.Formatter import Formatter
from utils.wrapper import trace


class StringFiltrator(AstVisitor):
    def __init__(self, method):
        super().__init__()
        self._tree = None
        self._params = [param["name"] for param in method["description"]["parameters"]]

    def visit(self, ast: Ast):
        self._tree = ast

    def result(self):
        return self._tree

    def visit_string(self, node: StringNode):
        string = " ".join(node.words)
        string = Filter.applyFiltersForString(string, self._params)
        node.words = string.split(" ")


class Statistic:
    def __init__(self):
        self.predicate_counters = {
            token: Value("i", 0) for token in tokens.predicates()
        }
        self.marker_counters = {
            token: Value("i", 0) for token in tokens.markers()
        }
        self.label_counters = {
            token: Value("i", 0) for token in tokens.labels()
        }
        self.num_raw_methods = None
        self.num_methods = None
        self.num_batches = None

    def count(self, token: Token):
        if isinstance(token, PredicateToken):
            counter = self.predicate_counters[token.name]
        elif isinstance(token, MarkerToken):
            counter = self.marker_counters[token.name]
        elif isinstance(token, LabelToken):
            counter = self.label_counters[token.name]
        else:
            raise ValueError(type(token))
        with counter.get_lock():
            counter.value += 1

    def num_tokens(self) -> int:
        predicates = sum(counter.value for counter in self.predicate_counters.values())
        markers = sum(counter.value for counter in self.marker_counters.values())
        labels = sum(counter.value for counter in self.label_counters.values())
        return predicates + markers + labels

    def show(self):
        method_concentration = self.num_methods / self.num_raw_methods * 100
        num_tokens = self.num_tokens()
        values = []
        counters = itertools.chain(
            self.predicate_counters.items(),
            self.marker_counters.items(),
            self.label_counters.items()
        )
        for token_name, counter in counters:
            number = counter.value
            concentration = number / num_tokens * 100
            values.append((token_name, number, concentration))
        values = sorted(values, key=lambda x: x[1], reverse=True)
        formatter = Formatter(("Name", "Number", "Concentration"), ("s", "d", "s"), (20, 20, 20), (0, 1, 2))
        formatter.print_head()
        formatter.print("raw methods", self.num_raw_methods, "")
        formatter.print("methods", self.num_methods, "%.1f%%" % method_concentration)
        formatter.print("batches", self.num_batches, "")
        formatter.print("tokens", num_tokens, "")
        formatter.print_delimiter()
        for token_name, number, concentration in values:
            formatter.print(token_name, number, "%.1f%%" % concentration)
        formatter.print_lower_delimiter()


statistic = Statistic()


@trace
def prepare_data_set():
    methods = Dumper.json_load(JODA_TIME_DATA_SET)
    statistic.num_raw_methods = len(methods)
    with Pool() as pool:
        methods = pool.map(apply, methods)
        methods = [method for method in methods if method is not None]
        statistic.num_methods = len(methods)
        methods = pool.map(build_batch, batching(methods))
        statistic.num_batches = len(methods)
    random.shuffle(methods, lambda: random.Random(SEED).uniform(0, 1))
    Dumper.pkl_dump(methods, ANALYSER_METHODS)
    statistic.show()


def apply(method):
    try:
        method = parse_contract(method)
        if len(method["contract"]) == 0: return None
        method = filter_contract(method)
        if len(method["contract"]) == 0: return None
        method = standardify_contract(method)
        method = filter_contract_text(method)
        method = index_contract(method)
        method = Filter.apply(method)
        if empty(method): return None
        method = join_java_doc(method)
        method = index_java_doc(method)
    except Exception:
        raise ValueError()
    return method


def standardify_contract(method):
    reducer = AstDfsGuide(AstEqualReducer())
    compiler = AstDfsGuide(AstCompiler())
    forest = (Parser.parse_tree(*args) for args in method["contract"])
    forest = (reducer.accept(tree) for tree in forest)
    method["contract"] = [compiler.accept(tree) for tree in forest]
    return method


def filter_contract(method):
    new_tokens = {tokens.POST_THIS.name, tokens.PRE_THIS.name, tokens.THIS.name, tokens.GET.name}
    contract = []
    for label, instructions, strings in method["contract"]:
        instructions_names = set(instruction.token.name for instruction in instructions)
        intersection = instructions_names & new_tokens
        # if tokens.NULL.name in instructions_names:
        if len(intersection) == 0:
            contract.append((label, instructions, strings))
    method["contract"] = contract
    return method


def vectorize(method) -> List[int]:
    result = []
    # result.extend(len(method["java-doc"][label]) for label in PARTS)
    # result.append(len(method["contract"]))
    length = []
    outputs_steps = len(method["contract"])
    for label, instructions, strings in method["contract"]:
        length.append(len(instructions))
    length = max(length)
    depth = int(np.ceil(np.log2(length)))
    output_type = os.environ['OUTPUT_TYPE']
    if output_type == "tree":
        length = 2 ** depth
    elif output_type in ("bfs_sequence", "dfs_sequence"):
        length += 1
    result.append(outputs_steps)
    result.append(length)
    return result


def chunks(iterable: Iterable[Any], block_size: int):
    result = []
    for element in iterable:
        result.append(element)
        if len(result) == block_size:
            yield result
            result = []
    if len(result) > 0:
        yield result


def batching(methods: Iterable[dict]):
    methods = ((vectorize(method), method) for method in methods)
    methods = sorted(methods, key=lambda x: np.linalg.norm(x[0]))
    methods = (method for vector, method in methods)
    return (chunk for chunk in chunks(methods, BATCH_SIZE) if len(chunk) == BATCH_SIZE)


def filter_contract_text(method):
    filtrator = AstDfsGuide(StringFiltrator(method))
    output_type = os.environ['OUTPUT_TYPE']
    if output_type in ("tree", "bfs_sequence"):
        compiler = AstBfsGuide(AstCompiler())
    elif output_type == "dfs_sequence":
        compiler = AstDfsGuide(AstCompiler())
    forest = (Parser.parse_tree(*args) for args in method["contract"])
    forest = (filtrator.accept(tree) for tree in forest)
    method["contract"] = [compiler.accept(tree) for tree in forest]
    return method


def parse_contract(method):
    forest = Parser.parse("\n".join(method["contract"]))
    compiler = AstDfsGuide(AstCompiler())
    method["contract"] = [compiler.accept(tree) for tree in forest]
    for label, instructions, strings in method["contract"]:
        for instruction in instructions:
            token = instruction.token
            statistic.count(token)
        statistic.count(label)
    return method


def index_contract(method):
    result = []
    for label, instructions, strings in method["contract"]:
        label = embeddings.labels().get_index(label.name)
        instructions = [
            embeddings.tokens().get_index(instruction.token.name)
            for instruction in instructions
        ]
        strings = {
            idx: [embeddings.words().get_index(word) for word in string]
            for idx, string in strings.items()
        }
        result.append((label, instructions, strings))
    method["contract"] = result
    return method


def index_java_doc(method):
    result = {}
    for label, text in method["java-doc"].items():
        split = (word.strip() for word in text.split(" "))
        indexes = tuple(embeddings.words().get_index(word) for word in split if len(word) > 0)
        result[label] = indexes
    method["java-doc"] = result
    return method


def build_batch(methods: List[dict]):
    inputs_steps = {label: max([len(method["java-doc"][label]) for method in methods]) for label in PARTS}
    docs = {label: [] for label in PARTS}
    docs_sizes = {label: [] for label in PARTS}
    pad = embeddings.words().get_index(PAD)
    for method in methods:
        for label in PARTS:
            line = list(method["java-doc"][label])
            docs_sizes[label].append(len(line))
            expected = inputs_steps[label] + 1 - len(line)
            line = line + [pad] * expected
            docs[label].append(line)
    for label in PARTS:
        docs[label] = np.transpose(np.asarray(docs[label]), (1, 0))
        docs_sizes[label] = np.asarray(docs_sizes[label])
    num_conditions = []
    sequence_length = []
    strings_lengths = [1]
    for method in methods:
        contract = method["contract"]
        num_conditions.append(len(contract))
        for raw_label, raw_instructions, raw_strings in contract:
            sequence_length.append(len(raw_instructions))
            strings_lengths.extend(len(string) for idx, string in raw_strings.items())
    string_length = max(strings_lengths)
    num_conditions = max(num_conditions)
    sequence_length = max(sequence_length)
    tree_depth = int(np.ceil(np.log2(sequence_length)))
    output_type = os.environ['OUTPUT_TYPE']
    if output_type == "tree":
        sequence_length = 2 ** tree_depth - 1
    strings_mask = []
    strings = []
    tokens = []
    labels = []
    nop = embeddings.tokens().get_index(NOP)
    empty_sequence = [nop] * sequence_length
    empty_string = [0] * string_length
    for method in methods:
        string_mask = [[0] * sequence_length for _ in range(num_conditions)]
        empty_strings = [[empty_string] * sequence_length for _ in range(num_conditions)]
        empty_tokens = [empty_sequence] * num_conditions
        empty_labels = [0] * num_conditions
        for i, (raw_label, raw_instructions, raw_strings) in enumerate(method["contract"]):
            empty_labels[i] = raw_label
            raw_instructions = raw_instructions + [nop] * (sequence_length - len(raw_instructions))
            empty_tokens[i] = raw_instructions
            for idx, raw_string in raw_strings.items():
                raw_string = raw_string + [pad] * (string_length - len(raw_string))
                empty_strings[i][idx] = raw_string
                string_mask[i][idx] = 1
        strings_mask.append(string_mask)
        strings.append(empty_strings)
        tokens.append(empty_tokens)
        labels.append(empty_labels)
    labels = np.asarray(labels)
    tokens = np.asarray(tokens)
    strings = np.asarray(strings)
    strings_mask = np.asarray(strings_mask)
    inputs = (docs, docs_sizes)
    outputs = (labels, tokens, strings, strings_mask)
    parameters = (num_conditions, sequence_length, string_length, tree_depth)
    return inputs, outputs, parameters


if __name__ == '__main__':
    init()
    prepare_data_set()
