import numpy as np

from app.genre_classification.labels import genre_id_for_model_label
from app.genre_classification.model_loader import GenreModels
from app.genre_classification.schema import GenrePrediction


def predict_by_svm(
    embedding: np.ndarray,
    models: GenreModels,
) -> GenrePrediction:

    encoded_label = models.svm_model.predict(embedding)[0]

    model_label = models.label_encoder.inverse_transform([encoded_label])[0]

    decision_scores = models.svm_model.decision_function(embedding)[0]

    score = (
        float(np.max(decision_scores))
        if np.ndim(decision_scores) > 0
        else float(abs(decision_scores))
    )

    return {
        "genre": genre_id_for_model_label(model_label),
        "score": score,
    }
