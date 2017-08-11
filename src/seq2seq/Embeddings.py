from typing import List, Dict, Tuple

import numpy as np
from contracts import Tokens, Types

from configurations.constants import EMBEDDING_SIZE
from configurations.paths import EMBEDDINGS
from configurations.tags import *
from utils import dumpers
from utils.wrappers import memoize


# ToDo: thread save
class Embeddings:
    def __init__(self, instance: List[Tuple[str, np.array]], default_name: str = None):
        self._instance = instance
        self.default_name = default_name

    @property
    def instance(self) -> List[Tuple[str, np.array]]:
        return self._instance

    @memoize.read_only_property
    def name2emb(self) -> Dict[str, np.array]:
        return {name: embedding for name, embedding in self._instance}

    @memoize.read_only_property
    def idx2name(self) -> List[str]:
        return [name for name, embedding in self.instance]

    @memoize.read_only_property
    def idx2emb(self) -> List[np.ndarray]:
        return [embedding for name, embedding in self.instance]

    @memoize.read_only_property
    def name2idx(self) -> dict:
        return {word: i for i, (word, embedding) in enumerate(self.instance)}

    def get_store(self, key):
        if key is None:
            raise ValueError
        if isinstance(key, (int, np.number)):
            index = int(key)
            if index < 0 or index >= len(self.instance):
                raise Exception("Store with index '%d' hasn't found" % key)
        elif isinstance(key, str):
            if key in self.name2idx:
                index = self.name2idx[key]
            elif self.default_name is not None:
                index = self.name2idx[self.default_name]
            else:
                raise Exception("Store with name '%s' hasn't found" % key)
        else:
            raise Exception("Key with type %s hasn't supported" % type(key))
        return index, self.idx2name[index], self.idx2emb[index]

    def get_index(self, key) -> int:
        return self.get_store(key)[0]

    def get_name(self, key) -> str:
        return self.get_store(key)[1]

    def get_embedding(self, key) -> np.array:
        return self.get_store(key)[2]

    def __len__(self):
        return len(self.instance)


@memoize.function
def words() -> Embeddings:
    instance = dumpers.pkl_load(EMBEDDINGS)
    instance[GO] = np.ones([EMBEDDING_SIZE], np.float32)
    instance[PAD] = np.zeros([EMBEDDING_SIZE], np.float32)
    instance = list(instance.items())
    instance.sort(key=lambda x: x[0])
    return Embeddings(instance, "UNK")


@memoize.function
def tokens() -> Embeddings:
    names = Tokens.instances[Types.OPERATOR] + Tokens.instances[Types.MARKER]
    names += (NOP, PARAM_0, PARAM_1, PARAM_2, PARAM_3, PARAM_4, PARAM_5, Types.STRING)
    embeddings = list(np.eye(len(names)))
    instance = list(zip(names, embeddings))
    return Embeddings(instance)
