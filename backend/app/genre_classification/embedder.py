import numpy as np
import torch
import torch.nn.functional as F

from app.genre_classification.model_loader import GenreModels


@torch.inference_mode()
def extract_clap_embedding(
    audio_chunks: list[np.ndarray], models: GenreModels, sample_rate: int
) -> np.ndarray:
    inputs = models.clap_processor(
        audio=audio_chunks,
        sampling_rate=sample_rate,
        return_tensors="pt",
        padding=True,
    )

    inputs = {
        key: value.to(models.device) if isinstance(value, torch.Tensor) else value
        for key, value in inputs.items()
    }

    outputs = models.clap_model.get_audio_features(**inputs)

    embeddings = outputs.pooler_output if hasattr(outputs, "pooler_output") else outputs

    # 각 chunk embedding 정규화
    embeddings = F.normalize(
        embeddings,
        p=2,
        dim=-1,
    )

    # chunk embedding mean pooling
    mean_embedding = embeddings.mean(dim=0)

    # mean pooling 결과 재정규화
    mean_embedding = F.normalize(
        mean_embedding,
        p=2,
        dim=0,
    )

    return mean_embedding.unsqueeze(0).cpu().numpy()
