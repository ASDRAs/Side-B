# Side-B Chrome Extension

Side-B 백엔드의 `/recommend` 응답을 확인하는 개발용 Chrome MV3 익스텐션입니다.

## 실행

1. 프로젝트 루트에서 백엔드를 실행합니다.

   ```powershell
   docker compose up --build
   ```

2. Chrome에서 `chrome://extensions`를 엽니다.
3. 우측 상단의 `개발자 모드`를 켭니다.
4. `압축해제된 확장 프로그램을 로드합니다`를 누릅니다.
5. 이 저장소의 `extension` 폴더를 선택합니다.
6. Side-B 아이콘을 열고 검색어를 입력한 뒤 `추천 요청`을 누릅니다.

기본 백엔드 주소는 `http://127.0.0.1:8000`입니다. 다른 주소를 사용한다면
`manifest.json`의 `host_permissions`에도 해당 origin을 추가한 뒤 익스텐션을
다시 로드해야 합니다.

## 테스트

확장 프로그램 단위 테스트는 Node 내장 테스트 러너로 실행합니다.

```powershell
cd extension
npm test
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
4. `manifest.json`의 `REPLACE_WITH_CHROME_EXTENSION_OAUTH_CLIENT_ID`를 발급받은
   Client ID로 바꿉니다.
5. 프로젝트 루트 `.env`에 서버 검색용 `YOUTUBE_API_KEY`와 임의의 긴
   `YOUTUBE_EXPORT_TOKEN`을 설정합니다.
6. Chrome의 확장 프로그램 화면에서 Side-B를 다시 로드합니다.
7. 팝업의 `YouTube 내보내기 토큰`에 같은 값을 입력합니다. 토큰은
   `chrome.storage.session`에만 보관되어 브라우저 세션이 끝나면 사라집니다.

OAuth scope는 `youtube.force-ssl` 하나만 사용합니다. access token은 백엔드나
`chrome.storage`에 저장하지 않고 Chrome Identity API의 메모리 캐시에 맡깁니다.
팀원마다 unpacked extension ID가 달라지면 같은 OAuth Client를 사용할 수 없으므로,
실계정 통합 전에 manifest의 공개 `key` 또는 Chrome Web Store Item ID로 개발용
extension ID를 먼저 고정해야 합니다.
