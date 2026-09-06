import hashlib
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import torch

from app.genre_classification.audio_preprocessing import split_audio
from app.genre_classification.classifier import predict_by_svm
from app.genre_classification.embedder import extract_clap_embedding
from app.genre_classification.model_loader import load_genre_models
from inference.main import InvalidAudio, Prediction

SAMPLE_RATE = 48_000
MAX_SECONDS = 35


def decode_audio(data: bytes) -> np.ndarray:
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-protocol_whitelist",
                "pipe",
                "-i",
                "pipe:0",
                "-t",
                "35.001",
                "-vn",
                "-ac",
                "1",
                "-ar",
                str(SAMPLE_RATE),
                "-f",
                "f32le",
                "pipe:1",
            ],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=20,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InvalidAudio("Audio could not be decoded within limits") from exc
    audio = np.frombuffer(result.stdout, dtype="<f4")
    if not audio.size or not np.isfinite(audio).all():
        raise InvalidAudio("Audio is empty or invalid")
    if audio.size > SAMPLE_RATE * MAX_SECONDS:
        raise InvalidAudio("Audio exceeds 35 seconds")
    return audio


class Predictor:
    def __init__(self):
        torch.set_num_threads(int(os.environ.get("TORCH_NUM_THREADS", "2")))
        torch.set_num_interop_threads(1)
        directory = Path(os.environ.get("MODEL_DIR", "/app/models"))
        manifest = json.loads((directory / "manifest.json").read_text())
        for name, expected in manifest["sha256"].items():
            with (directory / name).open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise RuntimeError(f"Model artifact checksum mismatch: {name}")
        self.model_version = manifest["model_version"]
        self.models = load_genre_models(
            directory / "clap",
            directory / "svm/svm_classifier.pkl",
            directory / "svm/label_encoder.pkl",
            device="cpu",
        )
        # Warm up before accepting requests and verify the model/SVM shape contract.
        self._predict(np.zeros(SAMPLE_RATE * 30, dtype=np.float32))

    def _predict(self, audio):
        embedding = extract_clap_embedding(
            split_audio(audio, SAMPLE_RATE, 30, 10), self.models, SAMPLE_RATE
        )
        value = predict_by_svm(embedding, self.models)
        return Prediction(**value, model_version=self.model_version)

    def __call__(self, data: bytes) -> Prediction:
        return self._predict(decode_audio(data))
