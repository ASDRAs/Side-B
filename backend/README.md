# Side-B 백엔드

[루트 README](../README.md) · [확장 프로그램](../extension/README.md) · 추론 서비스(`../inference/`)

추천, 미리듣기 탐색, YouTube 영상 매칭, 인증, 장르 분류 중계를 맡는 FastAPI 서비스다.

## 전체 흐름

```text
Chrome Extension
        │
        ├─ 추천 요청 ──────────────┐
        ├─ 미리듣기 요청           │
        ├─ YouTube 매칭 요청       ▼
        └─ 장르 분류 요청      FastAPI
                                  │
             ┌────────────────────┼────────────────────┐
             ▼                    ▼                    ▼
      Gemini / Last.fm      iTunes / Deezer      YouTube Data API
                                  │
                                  ▼
                         비공개 추론 서비스
```

백엔드는 추천과 매칭 결과만 반환한다. 사용자의 YouTube OAuth 토큰을 받지 않으며 플레이리스트도 직접 수정하지 않는다.

## 추천 흐름

`POST /recommend`에 `query`와 `top_n: 10`을 보낸다.

1. 인증 상태와 기능별 요청 한도를 확인한다.
2. Gemini가 입력을 곡 검색 또는 분위기 검색으로 나눈다.
3. 검색 유형에 맞춰 후보를 만들고 점수를 계산한다.
4. 방향 사이의 중복을 걷어 낸 뒤 최대 10곡씩 반환한다.

### 곡 검색

```text
아티스트 - 곡명
      │
      ├─ iTunes / Last.fm에서 기준 곡 확정
      ├─ similar: 닮은 곡
      ├─ reverse: 덜 알려진 닮은 곡
      └─ hidden: 닮은 아티스트의 숨은 곡
```

`similar → reverse → hidden` 순서로 계산한다. 앞 방향에 나온 곡은 뒤 방향에서 빼며, 한 방향의 계산만 실패했다면 그 방향만 빈 배열로 반환한다.

### 분위기 검색

```text
분위기 문장
   │
   ├─ Gemini가 검색 태그 생성
   ├─ similar: 태그와 가까운 곡
   ├─ opposite: 반대 무드의 곡
   └─ hidden: 후보 중 덜 알려진 곡
```

세 방향은 하나의 태그 추천 흐름에서 함께 만든다. 곡 검색과 달리 방향별로 실패를 격리하지 않는다.

실행하지 않은 방향은 응답에서 생략한다. 실행했지만 결과가 없는 방향은 빈 배열로 보내므로 클라이언트가 두 상태를 구분해야 한다.

## 미리듣기 흐름

미리듣기에는 `GET /preview` 또는 `GET /preview/stream`을 쓴다.

1. 곡명·아티스트 또는 공급자 ID로 곡을 확정한다.
2. iTunes를 먼저 조회하고 필요하면 Deezer로 대체한다.
3. `/preview`는 재생 정보를, `/preview/stream`은 백엔드를 거친 오디오를 반환한다.
4. 공급자 CDN에서 직접 재생하지 못하면 클라이언트가 스트림 경로로 전환한다.

두 엔드포인트는 인증 없이 열려 있다.

## YouTube 내보내기 흐름

`POST /exports/youtube/matches`에 추천 방향과 최대 10곡을 보낸다.

1. 서버 API 키로 곡마다 YouTube 후보를 검색한다.
2. 제목·아티스트·공식성·파생 버전 여부를 보고 점수를 매긴다.
3. 확실한 결과는 자동으로 고르고, 애매한 결과는 사용자가 확인하도록 돌려준다.
4. 같은 영상으로 매칭된 곡과 중복 입력을 제거한다.
5. 확장 프로그램이 사용자의 OAuth 토큰으로 실제 플레이리스트를 만들거나 곡을 추가한다.

백엔드는 사용자의 YouTube 계정에 쓰기 작업을 하지 않는다.

## 자동 EQ 분석 흐름

`POST /genre-classification`에 곡명과 아티스트를 보낸다.

1. Gemini가 미리듣기 검색 순서를 정한다.
2. iTunes 또는 Deezer에서 30초 미리듣기를 확보한다.
3. 음원 바이트를 `inference/` 서비스의 `POST /predict`로 보낸다.
4. 장르 ID, 점수, 모델 버전을 확장 프로그램에 돌려준다.
5. 확장 프로그램이 장르 ID에 맞는 EQ 프리셋을 적용한다.

`CLAP_INFERENCE_URL` 또는 `GEMINI_API_KEY`가 없으면 장르 분류만 503으로 실패한다. 추천 기능은 그대로 동작한다.

## 주요 API

| 경로 | 용도 | 인증 |
|---|---|---|
| `GET /health`, `GET /api/health` | 서비스 상태 확인 | 없음 |
| `GET /auth/config` | 인증 모드와 Firebase 프로젝트 확인 | 없음 |
| `GET /auth/me` | 로그인 계정 허용 여부 확인 | 필요 |
| `POST /recommend` | 곡·분위기 추천 | 필요 |
| `GET /preview`, `GET /preview/stream` | 미리듣기 탐색·중계 | 없음 |
| `POST /exports/youtube/matches` | YouTube 영상 후보 매칭 | 필요 |
| `POST /genre-classification` | 미리듣기 기반 장르 분류 | 필요 |

세부 요청·응답 스키마는 실행 중인 서버의 `/docs`에서 확인할 수 있다.

## 로컬 실행 흐름

### Docker Compose

```powershell
Copy-Item .env.example .env
docker compose up --build
```

저장소 루트에서 실행한다. 기본 주소는 `http://127.0.0.1:8000`, 상태 확인 경로는 `/api/health`다.

### Poetry

```powershell
cd backend
poetry install
poetry run uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Python 3.12 이상과 Poetry 2.x가 필요하다. 설정은 루트 `.env`와 `backend/.env` 순서로 읽으며 `backend/.env`가 우선한다.

## 인증 흐름

```text
클라이언트 요청
      │
      ▼
서버의 인증 모드 확인
      │
      └─ 설정된 인증 방식으로 사용자 확인
      │
      ▼
허용된 사용자 확인 → 기능별 요청 한도 적용 → API 실행
```

`/auth/config`는 클라이언트에 현재 인증 방식과 공개 설정을 알려 준다. 필요한 변수 이름과 기본값은 [`.env.example`](../.env.example)과 `app/config/`에 정리돼 있다.

## 확인 흐름

```powershell
cd backend
poetry install
poetry run pytest
poetry run ruff check .
poetry run ruff format --check .
```

기본 pytest 실행은 `backend/tests`, `deployment/tests`, `inference/tests`를 모두 검사한다. pre-commit은 `backend`에서 `poetry run pre-commit install`로 설치한다.

## 배포 흐름

운영 대상은 Cloud Run의 백엔드와 비공개 추론 서비스다. 설정 기준은 [`deployment/cloudrun.json`](../deployment/cloudrun.json)에 있다.

```text
이미지 빌드·레지스트리 푸시
        │
        ▼
deploy-clap.ps1 실행
        │
        ├─ 트래픽 없는 후보 리비전 생성
        ├─ IAM·타임아웃·환경 설정 대조
        └─ 후보 URL 출력
                │
                ▼
          실API 확인 후 승격
```

`deployment/deploy-clap.ps1`은 최초 구축용이 아니라 기존 서비스를 갱신하는 스크립트다. 실행 전에 두 Cloud Run 서비스, 운영 리비전, 서비스 계정, IAM, API 키와 인증 설정을 준비해야 한다. 추론 이미지 준비와 운영 정책은 추론 서비스 담당 문서를 따른다.
