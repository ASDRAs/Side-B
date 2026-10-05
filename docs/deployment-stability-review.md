# Side-B 배포본 안정성 점검

점검일: 2026-10-05 (한국 시간). 실제 서버 점검 종료 시각: 08:54:38 KST.

## 결론

두 운영 주소의 기본 상태와 주요 API 인증 거부는 정상 응답했다. 로컬 소스의 기존 테스트도 통과했다. 다만 배포 확장에서 worker 재시작 후 로그인 설정을 복원하지 않는 오류를 재현했다. 수정은 로컬 `Side-B-auth-fix` 체크아웃과 ZIP에 준비되어 있고 GitHub·운영 서비스에는 적용하지 않았다.

실제 Google/Firebase 로그인 성공, 승인된 사용자의 추천·장르 추론 성공, YouTube 실제 쓰기는 검증하지 못했다. Cloud Run 배포 리비전·환경변수·IAM·운영 로그·실제 부하 지표에 접근하지 못했으므로, 전체 배포본이 안정적이거나 보안 문제가 없다고 확정하지 않는다.

## 대상과 재현 환경

- GitHub main 기준: `d1da5cf1a2246082e608ba54ca8c160237a80d28`.
- 기존 확장: `C:/Users/dg203/Desktop/Side-B-1.0.0/extension`, v1.0.0. 인증 핵심 파일과 manifest의 blob SHA는 main과 일치하는 것을 앞선 점검에서 확인했다.
- 수정 체크아웃: `C:/Users/dg203/Desktop/Side-B-auth-fix`.
- 실제 태그 URL: `https://auth-20260915-011056---side-b-backend-7hmhv6htsa-du.a.run.app`.
- 실제 일반 URL: `https://side-b-backend-7hmhv6htsa-du.a.run.app`.
- Python 3.12.14의 격리된 `.venv-review`에서 pyproject 의존성 범위를 설치했다. 정확한 설치 버전은 `../outputs/auth-review/python-packages.txt`에 기록했다. 운영 의존성 및 운영 컨테이너와 동일하다는 의미는 아니다.
- 확장 테스트는 Node와 저장소의 bundle을 사용했다. npm lock 기반 의존성을 설치하여 인증 bundle 빌드도 성공했다. 빌드 결과의 차이는 라이선스 주석 배열 순서였고 최종 체크아웃에는 기존 bundle을 유지한다.

## 점검 결과와 증거

| 점검 | 결과 | 증거 |
|---|---|---|
| 실제 서버 기본 HTTP·인증 거부 | 16/16 예상 응답 | [원시 응답·시간](../outputs/auth-review/deployment-probes.json), [재실행 스크립트](../outputs/auth-review/probe_deployment.py) |
| 기존 v1.0.0 확장 단위 테스트 | 168/168 통과 | [로그](../outputs/auth-review/baseline-extension-tests.log) |
| worker 수정 후 확장 단위 테스트 | 171/171 통과 | [로그](../outputs/auth-review/extension-unit-tests.log) |
| 백엔드·배포 계약 테스트 | 501 통과, 6 경고 | [로그](../outputs/auth-review/backend-tests.log) |
| 추론 HTTP·분석 창 계약 테스트 | 9 통과 | [로그](../outputs/auth-review/inference-contract-tests.log) |
| worker 재시작 회귀를 기존 background에 적용 | 새 테스트 3개 실패, 화면과 같은 오류 재현 | [원본 재현 로그](../outputs/auth-review/baseline-worker-restart-repro.log) |
| Chromium 인증 E2E 8개 | 환경 문제로 실행 시작 불가 | [첫 실행](../outputs/auth-review/auth-e2e.log), [번들 Node 재시도](../outputs/auth-review/auth-e2e-bundled-node.log) |

실제 서버에는 각 주소에 대해 `/health`, `/auth/config`, `/auth/me`의 무인증·잘못된 토큰, 무인증 POST `/recommend`, `/genre-classification`, `/exports/youtube/matches`, `/openapi.json`을 한 번씩 요청했다. HTTP redirect는 따라가지 않았다. 인증이 필요한 POST에는 유효한 최소 구조의 시험 입력을 사용했고 모두 401로 거부됐다. 유효한 사용자 토큰은 사용하지 않았다. 운영 계정·DB·플레이리스트 쓰기와 부하 테스트는 하지 않았다.

`/health`는 모두 200, `/auth/config`는 모두 `mode=dual`과 `firebase_project_id=gen-lang-client-0392647514`를 반환했다. 잘못된 Firebase 토큰에는 `auth_unauthorized / Invalid Firebase ID token`을 반환했다. 공개 OpenAPI에 계정 신청·관리 API는 없었다.

첫 태그 URL health 요청은 5,641ms, 나머지 점검 요청은 78~969ms였다. 단일 표본이므로 콜드 스타트 여부·평균·p95 성능을 추정하지 않는다. `/health`는 단순 상태 응답이며 외부 공급자나 Firebase 인증·모델 실제 추론의 준비 상태까지 확인하지 않는다.

백엔드 경고는 Starlette/anyio의 deprecated alias와 테스트의 event loop 전환에 따른 async-lru 캐시 초기화 경고다. 테스트 실패나 실제 운영 장애 로그를 의미하지 않는다.

브라우저 자동화는 Chromium 설치 전 실행 파일 누락, 설치 후 `browserType.launchPersistentContext: spawn UNKNOWN`으로 중단됐다. 번들 Node에서도 같은 오류가 발생했다. 8개 시나리오가 애플리케이션 내부에서 실패했다는 증거는 아니며, 인증 UI E2E 통과로 집계하지 않는다. 이 E2E 자체도 Google/Firebase 통신을 fixture로 바꿔 사용하므로 실제 OAuth 성공 검증은 별도다.

## 확인한 문제와 승인 기능 도입 전 조치

### 높은 우선순위: worker 재시작 후 로그인 실패

`extension/background.js`는 새 worker마다 `createAuthManager()`로 초기 상태를 만든다. 사이드 패널이 열려 있는 동안 worker가 재시작되면, 패널은 이전 상태를 갖고 `AUTH_SIGN_IN`을 보낼 수 있다. 기존 background는 설정 복원 없이 `signIn()`을 호출하여 `authWorker.entry.js`의 설정 미준비 검사에서 `Google 로그인 설정을 먼저 확인하세요.`로 거부한다.

기존 단위 테스트 168개는 이 실제 background 연결 경로를 다루지 않아 통과했다. 새 cold-worker 테스트로 같은 문구를 재현했고, 로그인 전에 저장된 백엔드 설정을 복원하는 수정 후 전체 171개가 통과했다. 설정 조회 실패는 상태 응답으로 전달하며, 신뢰하지 않는 저장 주소는 네트워크·Google 인증 전에 거부한다. 실계정 화면의 최종 원인이 이 경로 하나뿐인지는 운영 Chrome에서 재확인해야 한다.

### 승인 정책 이관: 현재 dual 모드

실제 서버는 legacy 공유 토큰과 Firebase 로그인을 함께 받는 모드다. `backend/app/services/auth.py`의 legacy 분기는 Google 계정·허용 목록 검사와 별개의 경로다. 이는 현재 코드에 명시된 호환 동작이며, 운영 공유 토큰의 보유자나 설정값을 확인한 것은 아니다.

DB 승인 기능을 도입한 뒤에도 이 경로를 유지하면 사용자별 승인·차단 정책을 일관되게 적용할 수 없다. 운영 전환은 Firebase 전용 모드로 수행하고, 승인·차단과 DB 장애가 기존 환경변수·공유 토큰 허용으로 넘어가지 않게 테스트해야 한다.

### 승인 적용 범위: 현재 미리듣기는 공개

`backend/preview.py`의 `/preview`와 `/preview/stream`에는 인증 의존성이 없다. 후자는 외부 음원을 서버가 중계한다. 코드에서 확인한 사실이며, 정상 미리듣기 운영 요청으로 실제 비용이나 악용을 측정하지 않았다.

승인된 사용자만 전체 기능을 사용한다는 정책을 적용하려면 이 경로의 승인 검사도 필요하다. 현재 `<audio src>` 요청을 Bearer 인증 fetch와 Blob 재생으로 변경해야 하며 토큰을 URL에 넣어서는 안 된다. 공개 미리듣기를 의도적으로 유지한다면 승인 정책의 범위와 별도 사용량 제한을 명확히 정해야 한다.

### 배포 확인성: 태그·실제 리비전·기능 준비 상태

확장의 기본 API 주소는 날짜가 포함된 태그 URL이다. 실제 두 주소가 같은 코드를 사용하는지, 새 환경변수가 어느 리비전에 적용됐는지는 공개 응답만으로 확인할 수 없다. 단순 health와 OpenAPI는 build commit을 노출하지 않아 운영 컨테이너를 main 커밋과 매핑할 근거도 부족하다.

승인 기능 배포 시 실제 트래픽과 태그를 점검하고, 민감정보 없는 build commit/version 메타데이터를 운영 진단에 제공하는 것이 좋다. 단일 인스턴스별 요청 제한을 사용하는 현 구조에서 다중 인스턴스 전체 한도나 고부하 안정성은 이번 점검에 포함하지 않았다.

## 남은 운영 검증

1. 수정 ZIP을 실제 Chrome에 적용해 worker 재시작 전후 로그인·로그아웃을 검증한다.
2. 승인된 테스트 계정으로 실제 Firebase 로그인과 `/auth/me` 성공을 확인한다.
3. 같은 계정으로 추천·미리듣기·장르 분류를 각 1회 확인하고, 외부 공급자·추론 서버 오류와 타임아웃을 운영 로그에서 확인한다.
4. YouTube 테스트용 대상과 사용자 동의가 있는 환경에서 매칭·쓰기 동작을 확인한다.
5. Cloud Run 리비전, 태그, 트래픽, 환경변수 이름과 IAM, Firestore 규칙·DB 위치를 운영자가 확인한다. 비밀값을 소스나 진단 로그에 복사하지 않는다.
6. 새 승인 기능은 `auth-approval-design.md`의 권한·상태 전이 인수 테스트를 별도로 통과시킨다.
