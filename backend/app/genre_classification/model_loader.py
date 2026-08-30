from pathlib import Path

import joblib
import torch
from sklearn.preprocessing import LabelEncoder
from transformers import ClapModel, ClapProcessor

from app.genre_classification.schema import GenreModelLoadError, GenreModels


def load_genre_models(
    clap_model_path: str | Path,
    svm_model_path: str | Path,
    label_encoder_path: str | Path,
    device: str | None = None,
) -> GenreModels:
    clap_model_path = Path(clap_model_path)
    svm_model_path = Path(svm_model_path)
    label_encoder_path = Path(label_encoder_path)

    required_paths = (
        clap_model_path,
        svm_model_path,
        label_encoder_path,
    )

    for path in required_paths:
        if not path.exists():
            raise GenreModelLoadError(f"model artifact not found: {path}")

    selected_device = torch.device(
        device or ("cuda" if torch.cuda.is_available() else "cpu")
    )

    try:
        clap_processor = ClapProcessor.from_pretrained(
            clap_model_path,
            local_files_only=True,
        )

        clap_model = ClapModel.from_pretrained(
            clap_model_path,
            local_files_only=True,
        )

        clap_model = clap_model.to(selected_device)
        clap_model.eval()
        clap_model.requires_grad_(False)

        svm_model = joblib.load(svm_model_path)
        label_encoder = joblib.load(label_encoder_path)

    except Exception as exc:
        raise GenreModelLoadError("failed to load genre classification models") from exc

    if not hasattr(svm_model, "predict"):
        raise GenreModelLoadError("loaded SVM model does not provide predict()")

    if not isinstance(label_encoder, LabelEncoder):
        raise GenreModelLoadError("loaded label encoder is not a LabelEncoder")

    return GenreModels(
        clap_processor=clap_processor,
        clap_model=clap_model,
        svm_model=svm_model,
        label_encoder=label_encoder,
        device=selected_device,
    )
