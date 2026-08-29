import torch
import torch.nn.functional as F


@torch.inference_mode()
def extract_track_embedding(processor, clap_model, chunks, samle_rate, device):

    inputs = processor(
        audio=chunks,
        sampling_rate=samle_rate,
        return_tensors="pt",
        padding=True,
    ).to(device)

    outputs = clap_model.get_audio_features(**inputs)

    embeddings = outputs.pooler_output

    embeddings = F.normalize(
        embeddings,
        p=2,
        dim=-1,
    )

    mean_embedding = embeddings.mean(dim=0)

    mean_embedding = F.normalize(
        mean_embedding,
        p=2,
        dim=0,
    )

    return mean_embedding.cpu().numpy()
