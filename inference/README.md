## Genre Classification

Side-B의 장르 분류 모델은 **CLAP(Audio Encoder) + SVM** 구조를 사용합니다.

### Audio Processing

모델 학습에는 곡 전체를 그대로 사용하지 않고, 각 곡에서 장르적 특징이 잘 드러나는 **하이라이트 구간 약 30초**를 사용했습니다.

수집한 오디오는 CLAP 모델의 입력 형식에 맞게 전처리한 뒤 여러 구간으로 나누어 embedding을 추출합니다. 각 구간에서 얻은 embedding을 정규화하고 평균하여 곡 하나를 대표하는 하나의 audio embedding으로 변환합니다.

```text
Music
  ↓
30초 Highlight 추출
  ↓
48 kHz Mono 변환
  ↓
10초 단위 Chunk 분할
  ↓
CLAP Audio Embedding
  ↓
L2 Normalize
  ↓
Mean Pooling
  ↓
L2 Normalize
  ↓
SVM Classifier
  ↓
Genre
```

### Pre-trained Model

Audio embedding 추출에는 Hugging Face의 [`laion/clap-htsat-unfused`] 모델을 사용합니다.

- Model: `laion/clap-htsat-unfused`
- Architecture: CLAP (Contrastive Language-Audio Pretraining), HTSAT audio encoder
- License: Apache License 2.0
- Usage: Audio feature / embedding extraction

Side-B에서는 CLAP 모델 자체를 장르 분류기로 사용하지 않고, CLAP에서 추출한 audio embedding을 별도로 학습한 SVM classifier의 입력으로 사용합니다.

```text id="g9cbvz"
Audio
  ↓
laion/clap-htsat-unfused
  ↓
CLAP Audio Embedding
  ↓
SVM Classifier
  ↓
Genre
```

CLAP 및 사전 학습 모델의 저작권과 라이선스는 원 저작자 및 배포처의 라이선스를 따릅니다.

### Genre Labels

현재 모델은 총 10개의 장르를 분류합니다.

| Label | Model Label | API Genre ID |
|---:|---|---|
| 0 | POP | `pop` |
| 1 | R&B/Soul | `rnb_soul` |
| 2 | 댄스 | `dance` |
| 3 | 랩/힙합 | `hiphop` |
| 4 | 록/메탈 | `rock_metal` |
| 5 | 발라드 | `ballad` |
| 6 | 블루스 | `blues` |
| 7 | 재즈 | `jazz` |
| 8 | 컨트리 | `country` |
| 9 | 포크 | `folk` |

### Model

장르 분류에는 **CLAP + SVM** 구조를 사용합니다.

```text
Audio
   ↓
Pre-trained CLAP
   ↓
Audio Embedding
   ↓
SVM
   ↓
Genre
```

SVM은 학습 데이터에서 추출한 CLAP embedding과 장르 label을 이용해 학습하며, 서비스에서는 동일한 방식으로 생성한 embedding을 입력받아 최종 장르를 결정합니다.

사용되는 주요 모델 artifact는 다음과 같습니다.

```text
clap/
svm/
├── svm_classifier.pkl
└── label_encoder.pkl
```

- **CLAP**: 오디오에서 특징 embedding 추출
- **SVM Classifier**: CLAP embedding 기반 장르 분류
- **Label Encoder**: SVM class를 학습 장르 라벨로 변환