import numpy as np
from numpy import ndarray


def split_audio(
    audio: ndarray,
    sampe_rate: int,
    total_seconds:int,
    chunk_seconds:int,
):

    target_length = sampe_rate * total_seconds
    chunk_size = sampe_rate * chunk_seconds

    # 30초 미만이면 silence padding
    if len(audio) < target_length:
        audio = np.pad(
            audio,
            (0, target_length - len(audio)),
            mode="constant",
        )

    # 30초 이상이면 앞의 30초
    else:
        audio = audio[:target_length]

    chunks = [
        audio[start : start + chunk_size].astype(np.float32)
        for start in range(
            0,
            target_length,
            chunk_size,
        )
    ]

    return chunks
