from typing import List, Dict, Tuple

import numpy as np
from contracts.tokens import Tokens, Labels, Predicates, Markers
from contracts.tokens.MarkerToken import MarkerToken

from configurations.constants import EMBEDDING_SIZE
from configurations.paths import EMBEDDINGS
from configurations.tags import GO, PAD, NOP
from utils import dumpers
from utils.wrappers import lazy


# ToDo: thread save
class Embeddings:
    def __init__(self, instance: List[Tuple[str, np.array]], default_name: str = None):
        self._instance = instance
        self.default_name = default_name

    @property
    def instance(self) -> List[Tuple[str, np.array]]:
        return self._instance

    @lazy.read_only_property
    def name2emb(self) -> Dict[str, np.array]:
        return {name: embedding for name, embedding in self._instance}

    @lazy.read_only_property
    def idx2name(self) -> List[str]:
        return [name for name, embedding in self.instance]

    @lazy.read_only_property
    def idx2emb(self) -> List[np.ndarray]:
        return [embedding for name, embedding in self.instance]

    @lazy.read_only_property
    def emb2idx(self) -> Dict[Tuple[np.dtype], int]:
        return {tuple(embedding): index for index, (name, embedding) in enumerate(self.instance)}

    @lazy.read_only_property
    def name2idx(self) -> dict:
        return {word: i for i, (word, embedding) in enumerate(self.instance)}

    def get_store(self, key):
        if key is None:
            raise ValueError
        if isinstance(key, (int, np.number)):
            index = int(key)
            if index < 0 or index >= len(self.instance):
                raise Exception("Store with index '{}' is not found".format(key))
        elif isinstance(key, str):
            if key in self.name2idx:
                index = self.name2idx[key]
            elif self.default_name is not None:
                index = self.name2idx[self.default_name]
            else:
                raise Exception("Store with name '{}' is not found".format(key))
        else:
            key = tuple(key)
            if key in self.emb2idx:
                index = self.emb2idx[key]
            else:
                raise Exception("Store with embedding '{}' is not found".format(key))
        return index, self.idx2name[index], self.idx2emb[index]

    def get_index(self, key) -> int:
        return self.get_store(key)[0]

    def get_name(self, key) -> str:
        return self.get_store(key)[1]

    def get_embedding(self, key) -> np.array:
        return self.get_store(key)[2]

    def __len__(self):
        return len(self.instance)


@lazy.function
def words() -> Embeddings:
    instance = dumpers.pkl_load(EMBEDDINGS)
    instance[GO] = np.ones([EMBEDDING_SIZE], np.float32)
    instance[PAD] = np.zeros([EMBEDDING_SIZE], np.float32)
    instance = list(instance.items())
    instance.sort(key=lambda x: x[0])
    return Embeddings(instance, "UNK")


@lazy.function
def tokens() -> Embeddings:
    Tokens.register(MarkerToken(NOP))
    names = list(Predicates.names) + list(Markers.names)
    embeddings = list(np.eye(len(names)))
    instance = list(zip(names, embeddings))
    return Embeddings(instance)


@lazy.function
def labels() -> Embeddings:
    names = list(Labels.names)
    embeddings = list(np.eye(len(names)))
    instance = list(zip(names, embeddings))
    return Embeddings(instance)