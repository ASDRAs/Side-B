# Side-B Chrome Extension

Side-B 백엔드의 `/recommend` 응답을 확인하는 개발용 Chrome MV3 익스텐션입니다.

## 실행

1. Chrome에서 `chrome://extensions`를 엽니다.
2. 우측 상단의 `개발자 모드`를 켭니다.
3. `압축해제된 확장 프로그램을 로드합니다`를 누릅니다.
4. 이 저장소의 `extension` 폴더를 선택합니다.
5. 툴바의 Side-B 아이콘을 누르면 브라우저 오른쪽에 **사이드 패널**이 열립니다.
6. 처음 열면 로그인 화면이 표시됩니다. `Google로 계속하기`를 눌러 허용된
   계정으로 로그인합니다.
7. 로그인이 끝나면 검색어를 입력하고 `추천 요청`을 누릅니다.

팝업 대신 사이드 패널을 쓰는 이유는 팝업이 포커스를 잃는 순간 문서가 파괴되어
진행 중이던 추천 요청(최대 90초)이 함께 취소되기 때문입니다. 사이드 패널은 다른
탭으로 이동해도 유지되므로 YouTube Music을 들으면서 결과를 볼 수 있습니다.

검색어는 요청에 성공하면 저장되어 다음에 패널을 열 때 복원되고, 최근 5개가
입력란의 자동완성 목록에 뜹니다. 설정의 `지우기`로 기록을 비웁니다.

`현재 곡으로 추천`은 열려 있는 YouTube Music 탭에서 재생 중인 곡을 읽어 검색어에
채우고 곧바로 추천을 요청합니다. 채워진 검색어는 그대로 남으므로 고쳐서 다시
요청할 수 있습니다. 사이드 패널은 특정 탭에 묶이지 않으므로, 어떤 탭이
활성인지와 무관하게 서비스 워커가 YouTube Music 탭을 찾아 사용합니다.

기본 백엔드 주소는 배포된 Cloud Run 서비스
`https://auth-20260915-011056---side-b-backend-7hmhv6htsa-du.a.run.app`입니다.
다른 주소를 사용한다면
`manifest.json`의 `host_permissions`에도 해당 origin을 추가한 뒤 익스텐션을
다시 로드해야 합니다.

배포 URL은 클라이언트가 호출해야 하므로 비밀값이 아닙니다. 기본 배포는 Firebase
ID 토큰으로 `/recommend`, `/genre-classification`, `/exports/youtube/matches`를
보호합니다. ID 토큰은 확장 프로그램에 하드코딩하거나 사용자가 복사하지 않으며,
Google 로그인 뒤 Firebase SDK가 발급하고 갱신합니다. 설정의 `이전 서버 인증`은
`legacy` 또는 `dual` 모드로 운영되는 별도 서버와 연결할 때만 나타납니다.

### 로컬 백엔드 연결

프로젝트 루트에서 백엔드를 실행합니다.

먼저 `.env.example`을 `.env`로 복사하고 Gemini·Last.fm·YouTube 키를 채웁니다.
예시 파일은 `SIDE_B_AUTH_MODE=legacy`와
`ALLOW_UNAUTHENTICATED_RECOMMEND=false`로 인증을 유지합니다. 로컬에서 인증 없이
브라우저 요청을 테스트할 때만 복사한 `.env`에서
`ALLOW_UNAUTHENTICATED_RECOMMEND=true`로 변경합니다. Firebase 흐름까지 확인하려면
`SIDE_B_AUTH_MODE=firebase`, `FIREBASE_PROJECT_ID`, 허용 UID 또는 이메일을
설정합니다. 공개 배포에서는 인증 우회를 활성화하지 않습니다.

```powershell
docker compose up --build
```

사이드 패널 `설정` 안 `고급 설정`의 `백엔드 주소`를 `http://127.0.0.1:8000`
또는 `http://localhost:8000`으로 변경합니다. 두 로컬 주소는 개발용
`host_permissions`에 포함되어 있으며, 선택한 주소는 다음에 패널을 열 때도
유지됩니다.

## 테스트

테스트 실행에는 Node.js 20 이상이 필요합니다. 확장 프로그램 단위 테스트는 Node
내장 테스트 러너로 실행합니다.

```powershell
cd extension
npm test
```

## Extension E2E 테스트

Playwright가 테스트마다 빈 프로필에 실제 MV3 익스텐션을 로드하고, 고정 Extension
ID가 `hfcclomfoickmehgmdgjdjmiiekaciam`인지 확인합니다. 사이드 패널 문서는
`chrome-extension://<id>/sidepanel.html`을 탭으로 열어 검증합니다. 기본 추천 시나리오는
백엔드 응답을 고정하고 팀 토큰 헤더와 UI 렌더링을 검증하므로 배포 API 쿼터를 쓰지
않습니다.

YouTube 내보내기 시나리오는 기본적으로 추천·매칭 응답을 고정해 Extension의 토큰
전달과 매칭 검토 UI까지 재현합니다. Google OAuth와 실제 플레이리스트 생성 직전에는
취소하므로 사용자 계정은 변경하지 않습니다.

```powershell
cd extension
npm install
npx playwright install chromium
npm run test:e2e
```

각 시나리오만 따로 실행할 수도 있습니다.

```powershell
npm run test:e2e:recommend
npm run test:e2e:export
npm run test:e2e:persistence
```

영속화 시나리오는 검색어와 이전 서버용 토큰이 패널을 다시 열어도 남는지,
삭제가 되돌아오지 않는지, 여러 패널 사이에 변경이 전파되는지를 확인합니다.
이전 서버용 토큰은 입력 즉시 저장합니다. 이 동작은 Firebase를 사용하는 기본
배포의 로그인 세션과는 별개입니다.

브라우저 화면을 보면서 실행하려면 다음 명령을 사용합니다.

```powershell
npm run test:e2e:headed
```

배포된 `/recommend` 실호출은 별도 smoke test입니다. 현재 자동화는 Google 로그인
UI를 거치지 않으므로 `dual` 모드 배포의 레거시 토큰을 테스트 프로세스에만
전달해야 실행됩니다. Firebase 전용 배포는 수동 로그인 또는 별도의 테스트 사용자
토큰 발급 장치가 필요합니다.

```powershell
$env:SIDE_B_E2E_ACCESS_TOKEN = "<팀 백엔드 토큰>"
npm run test:e2e:deployed
Remove-Item Env:SIDE_B_E2E_ACCESS_TOKEN
```

배포된 `/exports/youtube/matches`까지 확인하려면 같은 환경변수를 설정한 상태에서
`npm run test:e2e:export`를 실행합니다. 매칭 검토 화면에서 취소하므로 Google OAuth와
플레이리스트 생성은 실행하지 않습니다. 두 실호출은 네트워크·외부 API 상태에
의존하므로 기본 E2E와 분리합니다. `SIDE_B_E2E_EXPORT_TOKEN`은 이전 이름과의 호환을
위해 내보내기 smoke test에서만 지원합니다.

다른 백엔드나 검색어를 사용할 때는 환경변수로 덮어쓸 수 있습니다. 백엔드 origin은
반드시 `manifest.json`의 `host_permissions`에도 있어야 합니다.

```powershell
$env:SIDE_B_API_BASE_URL = "https://example.run.app"
$env:SIDE_B_E2E_QUERY = "Radiohead - Creep"
npm run test:e2e:deployed
```

## YouTube Music 내보내기 설정

추천 조회만 사용할 때는 Google 설정이 필요하지 않습니다. 버킷별 `YouTube Music`
버튼으로 플레이리스트를 만들려면 다음 설정을 추가합니다.

내보내기 버튼을 누르면 백엔드가 선택한 YouTube 제목, 채널, 확신도를 먼저
표시합니다. 포함할 곡을 확인한 뒤 `플레이리스트 생성`을 눌러야 계정에 기록됩니다.
서비스 워커가 재시작되어 실행 중 작업이 사라진 경우, 남아 있는 진행 상태는 즉시
중단된 작업으로 표시됩니다.

1. Google Cloud 프로젝트에서 YouTube Data API v3를 활성화합니다.
2. OAuth 동의 화면을 구성하고 개발 중에는 팀원 계정을 테스트 사용자로 등록합니다.
3. Chrome Extension 유형의 OAuth Client를 생성합니다. Item ID에는 팀에서 고정해
   사용하는 확장 프로그램 ID를 입력합니다.
4. `manifest.json`의 공개 `key`가 만드는 extension ID와 OAuth Client의 Item ID가
   일치하는지 확인합니다.
5. 배포 백엔드에 서버 검색용 `YOUTUBE_API_KEY`를 설정합니다.
6. Firebase Authentication에서 Google 공급자를 활성화하고, 백엔드의
   `FIREBASE_ALLOWED_UIDS` 또는 `FIREBASE_ALLOWED_EMAILS`에 사용자를 등록합니다.
7. Chrome의 확장 프로그램 화면에서 Side-B를 다시 로드한 뒤 Google로 로그인합니다.

YouTube OAuth scope는 `youtube.force-ssl` 하나만 사용합니다. YouTube access token은
백엔드나 `chrome.storage`에 저장하지 않고 Chrome Identity API의 메모리 캐시에
맡깁니다. 백엔드 인증에 쓰는 Firebase ID 토큰과 플레이리스트를 생성하는 YouTube
OAuth 토큰은 용도와 수명이 다른 별도 자격 증명입니다.
팀원마다 unpacked extension ID가 달라지면 같은 OAuth Client를 사용할 수 없으므로,
실계정 통합 전에 manifest의 공개 `key` 또는 Chrome Web Store Item ID로 개발용
extension ID를 먼저 고정해야 합니다.
