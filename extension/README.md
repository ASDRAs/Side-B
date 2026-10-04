# Side-B 확장 프로그램

[루트 README](https://github.com/ASDRAs/Side-B/blob/main/README.md) · [백엔드](https://github.com/ASDRAs/Side-B/blob/main/backend/README.md) · 추론 서비스(`inference/`)

Chrome Manifest V3 사이드 패널에서 추천, YouTube Music 플레이리스트 내보내기, 자동 EQ를 제공하는 Side-B의 주 사용자 인터페이스.

이 문서는 배포 ZIP에도 포함됨. 앞부분은 사용자 흐름, [개발 흐름](#개발-흐름)부터는 개발·운영용 안내.

## 설치

Chrome 116 이상 필요.

1. `Side-B-1.0.0.zip` 압축 해제.
2. `chrome://extensions`에서 `개발자 모드` 활성화.
3. `압축해제된 확장 프로그램을 로드합니다` 선택.
4. 압축을 푼 `Side-B-1.0.0` 폴더 선택.
5. 확장 아이콘을 눌러 Side-B 사이드 패널 실행.

저장소에서 직접 설치할 경우 `extension` 폴더 선택. 인증 번들이 포함돼 있어 기본 기능 확인에는 별도 빌드 불필요.

## 시작 흐름

```text
사이드 패널 열기
       │
       ▼
백엔드 인증 방식 확인
       │
       ├─ Google 로그인 필요 ── 로그인과 허용 계정 확인
       └─ 이전 서버 방식 ───── 설정된 서버 자격 증명 확인
       │
       ▼
추천 화면 표시
```

기본 배포 서버는 허용 목록에 등록된 Google 계정만 사용 가능. 로그인에 성공해도 서버 허용 목록에 없는 계정은 접근 거부됨.

확장 프로그램이 신뢰하도록 미리 등록된 백엔드에만 인증 정보 전달. 임의의 서버 주소를 사용자 입력만으로 신뢰하지 않음.

## 추천 흐름

### 검색 시작

다음 두 방식 지원.

- `아티스트 - 곡명` 또는 분위기 문장 입력 후 `찾기` 선택.
- YouTube Music에서 곡 재생 후 `현재 곡으로 추천` 선택.

요청 중 `찾기` 버튼은 `취소`로 바뀜. 새 응답이 도착하기 전까지 이전 결과 유지.

### 결과 표시

```text
추천 응답
   │
   ├─ 곡 검색: 닮은 곡 / 덜 알려진 닮은 곡 / 숨은 발견
   └─ 분위기 검색: 닮은 곡 / 반대 무드 / 숨은 발견
            │
            ▼
       방향 탭 선택
            │
            ├─ YT Music에서 곡 확인
            └─ 이 곡에서 다시 탐색
```

서버가 실행하지 않은 방향은 탭을 만들지 않음. 실행했지만 결과가 0곡인 방향은 빈 탭으로 표시. 최근 검색어는 최대 5개까지 로컬 저장.

## YouTube Music 내보내기 흐름

```text
추천 방향 선택
      │
      ▼
N곡 플레이리스트로 내보내기
      │
      ▼
백엔드가 YouTube 영상 후보 검색·채점
      │
      ├─ 확실한 결과: 자동 선택
      ├─ 애매한 결과: 사용자 확인
      └─ 실패 결과: 제외 사유 표시
      │
      ▼
새로 만들기 / 기존에 추가
      │
      ▼
사용자 확인 후 저장
```

- 백엔드는 영상 후보만 찾으며 사용자의 YouTube 계정에 쓰기 작업을 하지 않음.
- 실제 플레이리스트 생성과 곡 추가는 확장 프로그램이 Chrome Identity로 받은 사용자 OAuth 권한으로 YouTube Data API에 직접 요청.
- 새 플레이리스트는 비공개로 생성.
- 애매한 매칭은 곡별 `YT Music` 링크로 직접 확인한 뒤 포함 여부 결정 가능.
- 저장 도중 일부 곡만 실패한 경우 성공·실패 개수를 분리 표시.

Google 로그인과 YouTube 쓰기 권한은 서로 다른 목적의 권한. 추천 로그인만으로 플레이리스트가 자동 생성되지 않으며, 저장 동작을 시작한 경우에만 YouTube 권한 요청.

## 자동 EQ 흐름

```text
YouTube Music 탭에서 EQ 적용
          │
          ▼
탭 오디오 캡처 시작
          │
          ├─ 현재 곡의 제목·아티스트 읽기
          └─ 백엔드에 장르 분류 요청
                     │
                     ▼
             30초 미리듣기 분석
                     │
                     ▼
              장르 ID 반환
                     │
                     ▼
        확장 내부 프리셋을 탭 오디오에 적용
```

캡처한 탭 오디오는 백엔드로 전송하지 않음. 백엔드는 별도로 찾은 iTunes·Deezer 미리듣기 음원만 추론 서비스에 전달.

곡이 바뀌면 제목과 아티스트를 다시 읽어 EQ 갱신. 분류 실패, 지원하지 않는 장르, 낮은 모델 점수에서는 원음 유지. YouTube Music 탭을 닫거나 다른 주소로 이동하면 캡처와 EQ 해제.

`chrome://` 페이지와 일반 웹페이지에는 EQ 적용 불가. 사용자가 직접 연 YouTube Music 탭에서만 캡처 가능.

## 설정 흐름

- **계정**: 현재 로그인 계정 확인과 로그아웃.
- **연결**: 현재 백엔드 주소와 연결 상태 확인.
- **EQ**: 자동 모드, 테스트 프리셋, 현재 적용 상태 확인.

백엔드 주소 변경은 개발·운영자가 manifest 권한과 신뢰 origin을 함께 구성한 경우에만 지원. 설정 화면에 주소를 입력하는 것만으로 새로운 서버가 신뢰되지 않음. 인증 정보가 포함된 요청을 알 수 없는 서버로 보내지 않음.

## 권한과 데이터 경계

| 권한 | 사용 목적 |
|---|---|
| `sidePanel` | Side-B UI 표시 |
| `storage` | 설정, 검색 기록, 진행 상태 저장 |
| `activeTab`, `scripting` | 사용자가 연 YouTube Music 탭의 현재 곡 정보 읽기 |
| `tabCapture`, `offscreen` | 탭 오디오 캡처와 Web Audio EQ 유지 |
| `identity` | Google 로그인과 사용자가 시작한 YouTube 저장 권한 획득 |
| 백엔드 host permission | 추천·매칭·장르 분류 API 호출 |
| YouTube·Firebase host permission | 플레이리스트 저장과 Google 인증 |

보안상 중요한 원칙:

- 팀 공유 토큰, API 키, 서비스 계정 키를 확장 소스나 배포 ZIP에 넣지 않음.
- 사용자의 YouTube OAuth 토큰을 Side-B 백엔드로 전송하지 않음.
- 신뢰 origin 목록을 넓힐 때 대상 서버의 소유권과 HTTPS 구성을 먼저 확인.
- 로컬 개발용 무인증 백엔드를 외부 네트워크에 공개하지 않음.

## 문제 해결

| 증상 | 확인 순서 |
|---|---|
| 로그인 후 화면 전환이 늦음 | 네트워크 상태 확인 후 패널 다시 열기 |
| 계정 접근 거부 | 기본 서버의 허용 계정인지 운영자에게 확인 |
| 추천 실패 | 설정의 백엔드 연결 상태와 로그인 상태 확인 |
| 현재 곡을 읽지 못함 | YouTube Music에서 실제 곡을 재생한 뒤 다시 시도 |
| EQ 적용 실패 | YouTube Music 탭 활성화 여부와 Chrome 캡처 권한 확인 |
| 일부 곡을 내보내지 못함 | 매칭 검토 화면의 제외 사유와 YouTube 링크 확인 |
| YouTube 권한 오류 | Chrome 계정과 YouTube 채널 상태 확인 후 다시 저장 |

## 개발 흐름

```text
소스 수정
   │
   ├─ npm test ───────── 단위·통합 계약 확인
   ├─ npm run test:e2e ─ UI 흐름 확인
   └─ Chrome에서 확장 새로고침 ─ 수동 확인
                              │
                              ▼
                    npm run release:zip
```

### 설치와 로드

```powershell
cd extension
npm install
npm run build
```

Chrome 확장 관리 화면에서 `extension` 폴더를 로드. 소스 변경 후 확장 새로고침과 사이드 패널 재실행 필요.

### 구성 요소 흐름

| 구성 요소 | 역할 |
|---|---|
| `sidepanel.html`, `sidepanel.css`, `sidepanel.js` | 사용자 동작과 화면 전환 |
| `scripts/auth*` | 로그인 상태와 백엔드 인증 요청 구성 |
| `background.js` | YouTube 저장, EQ 명령 조정, 서비스 워커 상태 관리 |
| `offscreen.html`, `offscreen.js` | Web Audio 캡처와 EQ 처리 |
| `scripts/eq*` | 장르 결과를 EQ 프리셋으로 변환 |
| `scripts/youtube*` | 현재 곡 읽기, 링크 생성, 내보내기 상태 표현 |

### 백엔드 연결

운영 서버 추가 시 두 경계를 함께 변경해야 함.

1. `manifest.json`의 `host_permissions`에 서버 origin 등록.
2. `auth.config.js`의 신뢰 백엔드 origin 등록.
3. 서버의 CORS와 인증 모드 구성.
4. 개발 빌드에서 로그인·추천·내보내기 흐름 확인.

구체적인 비밀값은 로컬 또는 배포 환경에서만 관리. 문서, Git, manifest에 기록하지 않음.

### 테스트

```powershell
cd extension
npm test
npm run test:e2e
```

주요 선택 실행:

```powershell
npm run test:e2e:recommend
npm run test:e2e:export
npm run test:e2e:persistence
```

실제 배포 서버를 호출하는 `*.live.e2e.cjs`는 별도 운영 검증용. 승인된 테스트 계정과 격리된 대상이 없는 환경에서는 실행하지 않음.

### 릴리스 ZIP

```powershell
cd extension
npm run release:zip
```

`outputs/releases/Side-B-<version>.zip` 생성. 패키징 전에 manifest와 package 버전 일치 여부 검사. ZIP에는 Side-B MIT `LICENSE`와 글꼴·아이콘의 별도 라이선스 포함.

## 라이선스

Side-B 자체 코드는 [MIT License](https://github.com/ASDRAs/Side-B/blob/main/LICENSE)로 배포. 배포 ZIP에서는 같은 폴더의 `LICENSE`에서 전문 확인 가능. Pretendard와 Lucide 등 서드파티 자산은 각 디렉터리의 라이선스 적용.
