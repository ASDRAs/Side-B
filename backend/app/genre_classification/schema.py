from dataclasses import dataclass
from typing import Any, TypedDict

import torch
from sklearn.preprocessing import LabelEncoder
from transformers import ClapModel, ClapProcessor


class GenrePrediction(TypedDict):
    genre: str
    score: float


class GenreModelLoadError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class GenreModels:
    clap_processor: ClapProcessor
    clap_model: ClapModel
    svm_model: Any
    label_encoder: LabelEncoder
    device: torch.device
