# Side-B 확장 프로그램

[루트 README](https://github.com/ASDRAs/Side-B/blob/main/README.md) · [백엔드](https://github.com/ASDRAs/Side-B/blob/main/backend/README.md) · 추론 서비스(`inference/`)

Side-B의 주 사용자 인터페이스다. Chrome Manifest V3 사이드 패널에서 추천, YouTube Music 플레이리스트 내보내기, 자동 EQ를 제공한다.

이 문서는 배포 ZIP에도 들어간다. 앞부분은 사용자를 위한 설명이며, [개발 흐름](#개발-흐름)부터는 개발·운영용 안내다.

## 설치

Chrome 116 이상이 필요하다.

1. `Side-B-1.0.0.zip`의 압축을 푼다.
2. `chrome://extensions`에서 `개발자 모드`를 켠다.
3. `압축해제된 확장 프로그램을 로드합니다`를 누른다.
4. 압축을 푼 `Side-B-1.0.0` 폴더를 선택한다.
5. 확장 아이콘을 눌러 Side-B 사이드 패널을 연다.

저장소에서 직접 설치한다면 `extension` 폴더를 선택한다. 인증 번들이 들어 있으므로 기본 기능을 확인할 때는 따로 빌드하지 않아도 된다.

## 시작 흐름

```text
사이드 패널 열기
       │
       ▼
백엔드 인증 방식 확인
       │
       ├─ Google 로그인 필요 ── 로그인 후 서버가 사용 승인 상태 확인
       │                         ├─ 승인됨 ─────────── 추천 화면
       │                         └─ 미등록·대기·거절·차단 ─ 승인 화면 (로그인 유지)
       └─ 이전 서버 방식 ───── 설정된 서버 자격 증명 확인
       │
       ▼
추천 화면 표시
```

서버가 승인 저장소(Firestore)를 쓰면, Google 로그인에 성공한 계정도 관리자가 승인해야 기능을 쓸 수 있다. 처음 로그인한 계정은 `사용 신청`을 누르고, 승인된 뒤 `승인 상태 확인`을 누르면 다시 로그인하지 않고 바로 사용할 수 있다. 거절·차단된 계정은 다시 신청해도 상태가 바뀌지 않는다. 승인 화면에는 인증 계정과 요청 서버가 표시된다. 이전 방식(허용 목록) 서버에서는 목록에 없는 계정이 로그인 단계에서 거부된다.

사용 중에 차단되면 다음 요청에서 승인 화면으로 돌아가며, 진행 중인 요청을 취소하고 결과와 EQ를 정리한다. 이미 만든 YouTube 플레이리스트는 지우지 않는다.

미리듣기는 로그인 정보로 서버에서 최대 8 MiB의 음원을 받아 재생한다. 로그인 정보는 주소에 넣지 않으며, 곡을 바꾸거나 로그아웃하면 받은 음원을 해제한다.

## 추천 흐름

### 검색 시작

검색은 두 가지 방식으로 시작할 수 있다.

- `아티스트 - 곡명` 또는 분위기 문장을 입력한 뒤 `찾기`를 누른다.
- YouTube Music에서 곡을 재생한 뒤 `현재 곡으로 추천`을 누른다.

요청이 진행되는 동안 `찾기` 버튼은 `취소`로 바뀐다. 새 응답이 도착하기 전까지는 이전 결과가 남아 있다.

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

서버가 실행하지 않은 방향은 탭을 만들지 않는다. 실행했지만 결과가 0곡인 방향은 빈 탭으로 표시한다. 최근 검색어는 로컬에 최대 5개까지 저장한다.

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

- 백엔드는 영상 후보만 찾는다. 사용자의 YouTube 계정에는 아무것도 쓰지 않는다.
- 플레이리스트 생성과 곡 추가는 확장 프로그램이 Chrome Identity로 받은 사용자 OAuth 권한으로 YouTube Data API에 직접 요청한다.
- 새 플레이리스트는 비공개로 만든다.
- 애매한 매칭은 곡별 `YT Music` 링크로 확인한 뒤 포함 여부를 정할 수 있다.
- 저장 도중 일부 곡만 실패하면 성공한 곡과 실패한 곡의 개수를 나눠 보여 준다.

Google 로그인과 YouTube 쓰기 권한은 쓰임이 다르다. 추천 기능에 로그인했다고 해서 플레이리스트가 자동으로 생기지는 않는다. YouTube 권한은 사용자가 저장을 시작했을 때만 요청한다.

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

캡처한 탭 오디오는 백엔드로 보내지 않는다. 백엔드는 별도로 찾은 iTunes·Deezer 미리듣기 음원만 추론 서비스에 전달한다.

곡이 바뀌면 제목과 아티스트를 다시 읽고 EQ를 갱신한다. 분류에 실패했거나 장르를 지원하지 않거나 모델 점수가 낮으면 원음을 유지한다. YouTube Music 탭을 닫거나 다른 주소로 이동하면 캡처와 EQ가 해제된다.

`chrome://` 페이지와 일반 웹페이지에는 EQ를 적용할 수 없다. 사용자가 직접 연 YouTube Music 탭에서만 캡처할 수 있다.

## 설정 흐름

- **계정**: 현재 로그인한 계정과 승인 상태를 확인하거나 로그아웃한다.
- **계정 관리**: 서버가 관리자로 지정한 계정에만 보인다. 대기·승인·거절·차단 목록을 25개씩 보고 승인·거절·차단·차단 해제·재심사를 한다. 다른 관리자가 먼저 처리했다면 최신 목록을 다시 불러온다. 메뉴 표시와 무관하게 서버가 매 요청 관리자 권한을 다시 확인한다.
- **연결**: 현재 백엔드 주소와 연결 상태를 확인한다.
- **EQ**: 자동 모드, 테스트 프리셋, 현재 적용 상태를 확인한다.

## 권한

| 권한 | 사용 목적 |
|---|---|
| `sidePanel` | Side-B UI 표시 |
| `storage` | 설정, 검색 기록, 진행 상태 저장 |
| `activeTab`, `scripting` | 사용자가 연 YouTube Music 탭의 현재 곡 정보 읽기 |
| `tabCapture`, `offscreen` | 탭 오디오 캡처와 Web Audio EQ 유지 |
| `identity` | Google 로그인과 사용자가 시작한 YouTube 저장 권한 획득 |
| 백엔드 host permission | 추천·매칭·장르 분류 API 호출 |
| YouTube·Firebase host permission | 플레이리스트 저장과 Google 인증 |

## 문제 해결

| 증상 | 확인 순서 |
|---|---|
| 로그인 후 화면 전환이 늦음 | 네트워크 상태 확인 후 패널 다시 열기 |
| 계정 접근 거부 | 표시된 오류 코드 확인. `auth_identity_unverified`는 Google 계정·이메일 인증 문제, `auth_account_denied`는 허용 목록 미포함 |
| 승인 대기·거절·차단 화면 | 관리자에게 승인 요청 후 `승인 상태 확인` |
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

Chrome 확장 관리 화면에서 `extension` 폴더를 로드한다. 소스를 바꾼 뒤에는 확장을 새로고침하고 사이드 패널을 다시 열어야 한다.

### 구성 요소 흐름

| 구성 요소 | 역할 |
|---|---|
| `sidepanel.html`, `sidepanel.css`, `sidepanel.js` | 사용자 동작과 화면 전환 |
| `scripts/auth*` | 로그인 세션·승인 상태 분리와 백엔드 인증 요청 구성(용도별 자격 증명) |
| `scripts/accessView.js`, `scripts/accessAdmin.js` | 승인 화면 상태와 관리자 계정 관리 API |
| `scripts/previewPlayer.js` | 인증 fetch 기반 미리듣기 Blob 재생·해제 |
| `background.js` | YouTube 저장, EQ 명령 조정, 서비스 워커 상태 관리 |
| `offscreen.html`, `offscreen.js` | Web Audio 캡처와 EQ 처리 |
| `scripts/eq*` | 장르 결과를 EQ 프리셋으로 변환 |
| `scripts/youtube*` | 현재 곡 읽기, 링크 생성, 내보내기 상태 표현 |

### 백엔드 연결

운영 서버를 추가할 때는 확장 프로그램과 백엔드 설정을 함께 변경해야 한다.

1. `manifest.json`의 `host_permissions`에 서버 origin을 등록한다.
2. `auth.config.js`에 신뢰할 백엔드 origin을 등록한다.
3. 서버의 CORS와 인증 모드를 구성한다.
4. 개발 빌드에서 로그인·추천·내보내기 흐름을 확인한다.

### 테스트

```powershell
cd extension
npm test
npm run test:e2e
```

필요한 시나리오만 따로 실행할 수도 있다.

```powershell
npm run test:e2e:recommend
npm run test:e2e:export
npm run test:e2e:persistence
```

실제 배포 서버를 호출하는 `*.live.e2e.cjs`는 운영 검증용이다. 승인된 테스트 계정과 격리된 대상이 없는 환경에서는 실행하지 않는다.

### 릴리스 ZIP

```powershell
cd extension
npm run release:zip
```

결과는 `outputs/releases/Side-B-<version>.zip`에 생성된다. 패키징 전에 manifest와 package 버전이 같은지 검사하며, ZIP에는 Side-B MIT `LICENSE`와 글꼴·아이콘의 별도 라이선스도 들어간다.

## 라이선스

Side-B 자체 코드는 [MIT License](https://github.com/ASDRAs/Side-B/blob/main/LICENSE)로 배포한다. 배포 ZIP에서는 같은 폴더의 `LICENSE`에서 전문을 확인할 수 있다. Pretendard와 Lucide 등 서드파티 자산에는 각 디렉터리의 라이선스가 적용된다.
