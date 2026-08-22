# Side-B Chrome Extension

Side-B 백엔드의 `/recommend` 응답을 확인하는 개발용 Chrome MV3 익스텐션입니다.

## 실행

1. Chrome에서 `chrome://extensions`를 엽니다.
2. 우측 상단의 `개발자 모드`를 켭니다.
3. `압축해제된 확장 프로그램을 로드합니다`를 누릅니다.
4. 이 저장소의 `extension` 폴더를 선택합니다.
5. Side-B 아이콘을 열고 검색어를 입력한 뒤 `추천 요청`을 누릅니다.

기본 백엔드 주소는 배포된 Cloud Run 서비스
`https://side-b-backend-7hmhv6htsa-du.a.run.app`입니다. 다른 주소를 사용한다면
`manifest.json`의 `host_permissions`에도 해당 origin을 추가한 뒤 익스텐션을
다시 로드해야 합니다.

### 로컬 백엔드 연결

프로젝트 루트에서 백엔드를 실행합니다.

```powershell
docker compose up --build
```

팝업의 `백엔드 주소`를 `http://127.0.0.1:8000` 또는
`http://localhost:8000`으로 변경합니다. 두 로컬 주소는 개발용
`host_permissions`에 포함되어 있으며, 선택한 주소는 다음 팝업에서도 유지됩니다.

## 테스트

테스트 실행에는 Node.js 20 이상이 필요합니다. 확장 프로그램 단위 테스트는 Node
내장 테스트 러너로 실행합니다.

```powershell
cd extension
npm test
```

## Extension E2E 테스트

Playwright가 번들 Chromium에 실제 MV3 익스텐션을 로드하고, 고정 Extension ID가
`hfcclomfoickmehgmdgjdjmiiekaciam`인지 확인합니다. 추천 시나리오는 배포된
`/recommend`를 호출한 뒤 HTTP 200 응답과 응답 곡 수만큼의 UI 렌더링을 검증합니다.
외부 추천 소스가 일부 곡을 제외할 수 있으므로 버킷당 정확히 10곡을 요구하지 않습니다.

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
```

브라우저 화면을 보면서 실행하려면 다음 명령을 사용합니다.

```powershell
npm run test:e2e:headed
```

배포된 `/exports/youtube/matches`까지 실제로 확인하려면 Cloud Run의
`YOUTUBE_EXPORT_TOKEN`과 같은 값을 테스트 프로세스에만 전달합니다. 이 모드도
매칭 검토 화면에서 취소하며 Google OAuth나 플레이리스트 생성은 실행하지 않습니다.

```powershell
$env:SIDE_B_E2E_EXPORT_TOKEN = "<팀 내보내기 토큰>"
npm run test:e2e:export
Remove-Item Env:SIDE_B_E2E_EXPORT_TOKEN
```

추천 E2E와 실백엔드 내보내기 E2E는 네트워크·외부 API 상태에 의존하는 smoke
test이므로 단위 테스트의 필수 CI 경로와 분리합니다.

다른 백엔드나 검색어를 사용할 때는 환경변수로 덮어쓸 수 있습니다. 백엔드 origin은
반드시 `manifest.json`의 `host_permissions`에도 있어야 합니다.

```powershell
$env:SIDE_B_API_BASE_URL = "https://example.run.app"
$env:SIDE_B_E2E_QUERY = "Radiohead - Creep"
npm run test:e2e
```

## YouTube Music 내보내기 설정

추천 조회만 사용할 때는 Google 설정이 필요하지 않습니다. 버킷별 `YouTube Music`
버튼으로 플레이리스트를 만들려면 다음 설정을 추가합니다.

내보내기 버튼을 누르면 백엔드가 선택한 YouTube 제목, 채널, 확신도를 먼저
표시합니다. 포함할 곡을 확인한 뒤 `플레이리스트 생성`을 눌러야 계정에 기록됩니다.
서비스 워커가 재시작되어 실행 중 작업이 사라진 경우, 남아 있는 진행 상태는 즉시
중단된 작업으로 표시됩니다.

`YOUTUBE_EXPORT_TOKEN`은 Google에서 사용자마다 발급받는 값이 아닙니다. Side-B
운영자가 서버 검색 API를 보호하기 위해 만드는 **팀 공용 토큰**이며, 사용자별
Google 계정 인증은 Chrome Identity OAuth가 별도로 처리합니다. 개발용 토큰은 다음과
같이 생성할 수 있습니다.

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

출력값을 Cloud Run의 `YOUTUBE_EXPORT_TOKEN` 환경변수 또는 Secret에 설정하고,
팀원에게 안전한 채널로 전달합니다. 팀원은 팝업의 `팀 내보내기 토큰`에 같은 값을
입력합니다. 이 값은 저장소나 공개 문서에 커밋하면 안 됩니다. 공개 사용자 대상
서비스로 전환할 때는 이 공용 토큰 대신 사용자 인증 기반 접근 제어로 교체해야 합니다.

1. Google Cloud 프로젝트에서 YouTube Data API v3를 활성화합니다.
2. OAuth 동의 화면을 구성하고 개발 중에는 팀원 계정을 테스트 사용자로 등록합니다.
3. Chrome Extension 유형의 OAuth Client를 생성합니다. Item ID에는 팀에서 고정해
   사용하는 확장 프로그램 ID를 입력합니다.
4. `manifest.json`의 공개 `key`가 만드는 extension ID와 OAuth Client의 Item ID가
   일치하는지 확인합니다.
5. 배포 백엔드에 서버 검색용 `YOUTUBE_API_KEY`를 설정합니다.
6. 위 명령으로 만든 값을 Cloud Run의 `YOUTUBE_EXPORT_TOKEN`에 설정합니다.
7. Chrome의 확장 프로그램 화면에서 Side-B를 다시 로드합니다.
8. 팝업의 `팀 내보내기 토큰`에 같은 값을 입력합니다. 토큰은
   `chrome.storage.session`에만 보관되어 브라우저 세션이 끝나면 사라집니다.

OAuth scope는 `youtube.force-ssl` 하나만 사용합니다. access token은 백엔드나
`chrome.storage`에 저장하지 않고 Chrome Identity API의 메모리 캐시에 맡깁니다.
팀원마다 unpacked extension ID가 달라지면 같은 OAuth Client를 사용할 수 없으므로,
실계정 통합 전에 manifest의 공개 `key` 또는 Chrome Web Store Item ID로 개발용
extension ID를 먼저 고정해야 합니다.
