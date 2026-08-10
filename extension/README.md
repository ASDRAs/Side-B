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
