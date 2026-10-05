# Side-B 관리자 계정 승인 설계

작성일: 2026-10-05 (한국 시간). 같은 날 로컬 체크아웃에 구현을 반영해 문서를 갱신했다. Firestore 생성, 규칙·IAM 적용, 관리자 권한 부여, 부트스트랩 실행, 운영 배포는 수행하지 않았다. 운영 절차는 [auth-approval-operations.md](auth-approval-operations.md), 설계 검토는 [auth-approval-opus-review.md](auth-approval-opus-review.md)에 있다.

## 목표와 구조

확장 프로그램에서 신청·승인·차단을 관리하여 일반 사용자 추가 시 재배포를 없앤다. 관리자 화면과 API 경로가 공개돼도 일반 사용자는 관리 작업을 할 수 없어야 한다.

기존 `backend/app/services/auth.py`는 Firebase 토큰 검증 뒤 `FIREBASE_ALLOWED_UIDS`/`FIREBASE_ALLOWED_EMAILS`를 검사했고, 확장의 `validate()`는 `/auth/me` 성공을 로그인 완료 조건으로 삼아 실패 시 Firebase에서 로그아웃했다. 그래서 신원 인증과 서비스 사용 승인을 분리했다.

- 서버 설정 `SIDE_B_ACCESS_STORE=env`(기본값)는 기존 환경변수 허용 목록·legacy·dual 동작을 그대로 유지한다.
- `SIDE_B_ACCESS_STORE=firestore`는 Firebase 신원만 확인하고, 사용 승인은 Firestore `access_users/{uid}`에서 읽는다. 이 모드는 `SIDE_B_AUTH_MODE=firebase`와 비어 있지 않은 `SIDE_B_ADMIN_UIDS`가 없으면 기동하지 않으며, 환경변수 허용 목록·공유 토큰을 승인 근거로 쓰는 경로가 없다.

점검 당시 실제 서버의 `/auth/config`는 `dual` 모드였다. 운영 전환은 Firebase 전용 + Firestore 모드로 한다.

## 권한 원칙

1. 확장 프로그램은 신뢰하지 않는다. 숨긴 버튼, 화면의 관리자 표시, 요청 본문의 이메일·역할·승인자 값을 권한 근거로 쓰지 않는다. 본문 스키마는 `extra="forbid"`로 그런 필드를 422로 거부한다.
2. 모든 관리 API는 Firebase ID 토큰의 서명·프로젝트·만료·취소를 검사하고, Google 제공자·인증된 이메일을 확인한 뒤 토큰 UID를 서버 설정 `SIDE_B_ADMIN_UIDS`와 대조한다. 같은 작업 ID의 재전송(멱등 재사용)도 이 검사를 먼저 통과해야 한다.
3. 관리자 추가·제거는 서버 설정 변경이다. 일반 결정 API로는 관리자 계정의 상태를 바꿀 수 없다(403 `access_admin_target_protected`).
4. 최초 관리자 후보는 사용자가 언급한 `asdra030522@gmail.com`이다. 실제 Firebase UID는 확인되지 않았으므로 문서나 코드에 임의 값을 넣지 않았다.
5. 요청자 UID는 검증된 토큰에서만 얻는다. 경로의 `{uid}`는 승인 대상이며 문서 ID 규칙(1~128자, `/` 금지, `.`/`..`·`__예약__` 금지, 제어문자 금지)으로 검증한다. ASCII 전용 정규식은 쓰지 않는다.
6. 확장은 Firestore에 직접 접근하지 않는다. 클라이언트 규칙은 deny-all(`deployment/firestore/firestore.rules`)이고, 서버 SDK는 Cloud Run 서비스 계정의 ADC를 쓴다. 서버 SDK는 규칙을 우회하므로 API 권한 검사가 별도로 필수다.
7. 서비스 계정 키·공유 관리자 토큰을 확장, 저장소, 로그에 두지 않는다. 감사 기록에도 토큰·헤더가 없다.

근거: [OWASP Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html), [Firestore 서버 SDK와 Security Rules](https://firebase.google.com/docs/firestore/security/rules-structure).

## 계정 상태와 전이

| 상태 | 사용자 화면 | 허용 작업 |
|---|---|---|
| unregistered (문서 없음) | 사용 신청 안내 | 신청, 본인 상태 조회, 로그아웃 |
| pending | 관리자 승인 대기, 신청 시각 | 본인 상태 조회, 로그아웃 |
| approved | 일반 Side-B 화면 | 추천, 미리듣기, EQ 분석, 내보내기 |
| rejected | 신청이 거절됨 | 본인 상태 조회, 로그아웃 |
| blocked | 이용이 차단됨 | 본인 상태 조회, 로그아웃 |

| 관리자 action | 전이 |
|---|---|
| `approve` | pending → approved |
| `reject` | pending → rejected |
| `block` | approved → blocked |
| `unblock` | blocked → approved (UI: 차단 해제) |
| `reopen` | rejected → pending (UI: 재심사) |

`unregistered → pending`은 본인의 신청으로만 생긴다. 반복 신청은 기존 상태를 그대로 반환하며 신청 시각·결정 결과를 덮어쓰지 않는다. 따라서 rejected·blocked 계정은 재신청으로 풀리지 않는다. 그 밖의 조합은 409 `access_invalid_transition`이다.

## 사용자 흐름

1. Google → Firebase 로그인 후 서버가 신원을 검증한다.
2. `/auth/me`는 신원이 유효하면 200으로 `access_status`, `access_requested_at`, `access_store`, `can_manage_access`를 반환한다. 미승인 상태는 로그아웃 사유가 아니다. 신원 실패만 401/403이다.
3. 미등록 사용자는 "사용 신청"을 누른다. 본문은 비어 있고, 서버가 토큰의 UID·이메일·표시 이름으로 pending 문서를 만든다.
4. 대기 화면은 인증 계정·요청 서버·신청 시각과 "승인 상태 확인"(수동), 로그아웃을 보여 준다. 자동 폴링·실시간 구독은 없다.
5. 관리자가 승인하면 다음 상태 확인에서 같은 Firebase 세션으로 일반 화면이 열린다.
6. 차단되면 다음 기능 요청이 403 `access_not_approved`를 받는다. 확장은 승인 상태를 다시 읽고, 승인이 사라졌으면 세션 세대를 바꿔 진행 요청을 취소하고 결과·계정별 캐시를 비우고 EQ를 멈춘다. 이미 내려받은 데이터나 만든 YouTube 플레이리스트는 소급 삭제하지 않는다.

`authWorker.entry.js`는 로그인 세션(`status`)과 승인 상태(`access`)를 따로 보관한다. `credential()`은 용도(`feature`/`access`/`admin`)를 받아 미승인 세션에는 `access` 용도 토큰만, `can_manage_access` 세션에만 `admin` 용도 토큰을 준다. 이 로컬 제한은 보조이며 최종 판단은 항상 서버가 한다. 토큰과 Google access token은 상태 브로드캐스트·로그·저장소에 넣지 않는다.

## 관리자 화면

기존 사이드 패널 설정에 "계정 관리"를 두었다. 서버가 `can_manage_access=true`를 준 세션에만 보이며, 승인 대기 화면에서도 관리자에게는 "계정 관리 열기"가 보인다.

- 대기·승인·거절·차단 탭, 이메일·표시 이름·UID·신청 시각, 상태별 허용 action 버튼. 관리자 계정 행에는 버튼이 없다.
- 25개씩 커서 페이지(서버 상한 50). 설정을 처음 열 때와 새로고침·탭 변경 때만 조회한다.
- 작업 중에는 모든 버튼을 비활성화한다. 실패 시 성공으로 표시하지 않고, 재시도는 같은 작업 ID·revision을 재사용한다(이미 커밋된 결정은 재생된다).
- 409와 404 `access_user_not_found`는 최신 목록을 다시 불러오고 "이미 처리되었거나 다른 관리자가 먼저 변경"을 알린다. 403 `admin_required`·401은 관리 화면을 닫고 상태를 다시 읽는다.
- 계정 변경·로그아웃·worker 재시작(세션 세대 변경)이나 관리자 플래그 상실 시 목록·작업 상태를 버린다.
- 외부 문자열은 `textContent`로만 넣는다.

## DB 모델

Firestore Standard, 사용자별 문서 하나. 기존 DB가 있으면 컬렉션만 추가한다(위치·존재 미확인).

`access_users/{uid}`: `uid`, `email`, `display_name`(검증된 토큰에서 복사), `status`, `revision`(정수, 생성 시 1, 결정마다 +1), `requested_at`, `updated_at`, `decided_at`, `decided_by`(관리자 UID). 시각은 백엔드 서버 시계(UTC).

`access_audit/{관리자UID}:{operation_id}`: 대상 UID, action, 기대 revision, 이전·이후 상태와 revision, 요청자 UID, 시각, 작업 ID. 멱등 범위가 요청자 UID로 한정되며, 문서 ID가 결정적이고 `create()`로 쓰므로 트랜잭션 재시도나 재전송이 감사 기록을 중복 생성할 수 없다.

`access_request_quota/{UTC 날짜}`: 전역 신규 신청 수. 신청 트랜잭션 안에서 상한(`ACCESS_REQUEST_DAILY_LIMIT`)을 검사·증가한다.

결정은 한 트랜잭션에서 감사 문서와 대상 문서를 읽고, 같은 작업이면 저장된 결과를 재생(쓰기 없음)하고, 다른 내용의 같은 작업 ID면 409 `access_operation_conflict`, revision이 다르면 409 `access_revision_conflict`(현재 상태·revision 포함), 그 밖에는 상태 갱신과 감사 생성을 함께 커밋한다. 감사 쓰기가 실패하면 상태도 바뀌지 않는다. 손상된 문서(알 수 없는 status, 잘못된 revision)는 승인으로 해석하지 않고 503이다.

## API와 오류 계약

| API | 인증·권한 | 응답·동작 |
|---|---|---|
| GET /auth/config | 공개 | 공개 설정만 |
| GET /auth/me | 검증된 Google Firebase 신원 | 본인 정보, 승인 상태, 관리자 메뉴 표시 여부 |
| POST /access/request | 검증된 신원 | 본인 신청만 생성, 반복은 기존 상태. 본문 필드 금지 |
| GET /admin/access-users?status&limit&cursor | 서버 관리자 UID | 상태 필터, 기본 25·최대 50, 불투명 커서(766자 이하, 상태에 묶임) |
| POST /admin/access-users/{uid}/decision | 서버 관리자 UID | `{action, expected_revision, operation_id(UUID)}`만 허용 |
| POST /recommend, /genre-classification, /exports/youtube/matches, GET /preview, /preview/stream | 검증된 신원 + approved (Firestore 모드) | 기존 기능. env 모드의 미리듣기는 기존처럼 공개 |

| 상태 | code | 의미 |
|---|---|---|
| 401 | `auth_unauthorized` | 토큰 없음·형식 오류·무효·만료·취소, legacy 토큰(Firebase 전용 모드) |
| 403 | `auth_identity_unverified` | Google 제공자가 아니거나 이메일 미인증 (신원 거절 → 확장은 로그아웃) |
| 403 | `auth_account_denied` | env 모드 허용 목록 미포함 (기존 동작) |
| 403 | `access_not_approved` + `access_status` | 기능 사용 미승인 (세션 유지) |
| 403 | `admin_required` | 일반 사용자의 관리 API 호출 |
| 403 | `access_admin_target_protected` | 관리자 계정 대상 결정 |
| 404 | `access_user_not_found` / `access_management_disabled` | 신청 기록 없음 / env 모드 |
| 409 | `access_revision_conflict` / `access_invalid_transition` / `access_operation_conflict` | 처리 충돌 |
| 413 | `request_too_large` | `/access/`, `/admin/` 본문 4 KiB 초과 |
| 422 | 검증 오류 / `access_invalid_input` | 잘못된 UID·작업 ID·action·revision·커서·limit, 금지 필드 |
| 429 | `auth_rate_limited` / `access_request_quota_exceeded` | 요청 제한 / 일일 신청 상한 |
| 503 | `access_store_unavailable` / `auth_verification_unavailable` / `auth_configuration_error` | DB 장애·시간 초과·손상, 인증 확인 장애, 설정 오류 |

DB 장애를 미승인으로 덮거나 환경변수·공유 토큰으로 우회하지 않는다. DB 호출은 인스턴스당 동시 8개, 호출당 8초로 제한하며(설정 가능), 시간 초과된 호출도 끝날 때까지 자리를 차지한다.

커서 상한은 유효 UID 128자의 최대 UTF-8 512바이트와 상태·UTC 시각·JSON 구문 62바이트를 합한 574바이트를 padding 없는 base64url로 인코딩한 766자다. `MAX_CURSOR_LENGTH`를 인코더·디코더·HTTP Query 검증에서 공유한다. 한글·4바이트 문자의 128자 UID도 다음 페이지로 이동하며, 잘못된 base64·필터 변경·상한 초과는 거부한다.

확장은 Firebase 계정 관찰 시 이전 승인·관리자 상태와 진행 중 갱신을 즉시 폐기한다. 토큰 갱신 공유는 계정 객체·UID·로그인/승인 세대·서버에 묶이며, SDK 관찰이 늦어도 검증된 계정과 `auth.currentUser`가 일치해야 자격 증명을 반환한다. 관찰·수동 상태 확인·신청 응답은 공통 순서로 검사하고, 패널도 worker의 `stateRevision`보다 오래된 상태 응답을 버린다.

명시적 사용 신청·상태 확인이 토큰을 확보하는 동안에는 동일 SDK User의 토큰 observer 조회를 그 작업에 합친다. `getIdToken()` 자체가 observer를 발생시켜도 첫 신청은 실제 POST를 보내며, 상태 확인의 401 강제 토큰 갱신도 동일하게 처리한다. 토큰 확보가 끝난 뒤의 새 observer는 기존 공통 순서대로 이전 HTTP 응답을 무효화한다. 계정 객체·UID·세대·서버 변경, 로그아웃, 명시적 기능·관리자 거부는 토큰 확보 중에도 진행 요청을 무효화하며, 동시 신청·상태 확인은 기존 Promise를 공유한다.

기능의 `403 access_not_approved`와 관리의 `403 admin_required`는 인증 fetch가 사용한 자격 증명 세대·origin을 포함해 `AUTH_REPORT_DENIAL`로 보고한다. background는 발신 문서·용도·오류 코드·origin·현재 세대를 검사한다. 현재 기능 거부는 즉시 사용 승인과 결과·Blob·EQ를 회수하고, 관리자 거부는 관리자 권한·목록·재시도 상태만 회수한다. Firebase 신원 세션은 유지하며, 뒤따른 `/auth/me`의 429·503·네트워크 오류로 이전 권한을 복원하지 않는다. 새 계정·새 worker로 넘어간 뒤 도착한 이전 거부는 적용하지 않는다.

요청 제한(인스턴스 로컬): `/auth/me`(`access_status`), 기능·미리듣기 전 승인 조회(`access_lookup`, 사용자별 상한이 DB 읽기를 묶음), 신청(`access_request`), 관리 조회·결정(`admin_read`·`admin_write`), 미리듣기(`preview`). 기능별 예산은 승인된 사용자에게만 차감되어 미승인 계정이 소진할 수 없다. 인스턴스 로컬 한계와 `--max-instances`·비용 조건은 운영 문서 8절에 있다. 승인 조회 캐시는 두지 않아 차단은 다음 요청부터 적용된다.

## 미리듣기 경로

Firestore 모드에서 `/preview`, `/preview/stream`은 승인 검사를 한다. `<audio src>`는 Bearer 헤더를 붙일 수 없으므로 확장은 인증 fetch로 `/preview/stream` 바이트를 최대 8 MiB까지 받아(`Content-Length`와 실제 수신량 모두 검사, `audio/*`만 허용) Blob URL로 재생한다. 서버도 중계를 8 MiB에서 끊는다. 토큰은 URL에 넣지 않는다. 곡 전환·로그아웃·승인 상실 시 fetch를 취소하고 늦은 응답을 버리며 Blob URL을 해제한다. 공급자 CDN 음원 자체의 접근은 Side-B가 통제하지 않는다.

## 이관과 배포

운영 절차 문서의 5~9절을 따른다. 요점:

1. 운영 UID·기존 허용 목록·트래픽 리비전·태그·DB 존재를 확인한다. 이번 작업 환경에는 운영자 권한과 유효한 사용자 토큰이 없어 확인하지 않았다.
2. 에뮬레이터 통합 테스트로 트랜잭션·규칙을 확인한다(이번 환경에서는 미실행).
3. 규칙·색인·IAM을 적용하고 `backend/scripts/bootstrap_access.py`로 관리자·기존 허용 계정을 approved로 이관한다(드라이런 기본, `--apply` 필요, 기존 문서 덮어쓰기 없음).
4. Firestore 모드 리비전을 트래픽 없이 배포·검증하고, 새 확장을 함께 배포한다. 옛 확장은 pending과 Blob 미리듣기를 처리하지 못한다.
5. 확장의 현재 기본 API 주소는 일반 서비스 URL `https://side-b-backend-1073342688292.asia-northeast3.run.app`이다(이전 문서의 "날짜 태그 URL" 전제는 틀렸다). 날짜 태그 URL·옛 일반 URL은 저장값 이관 대상으로만 남아 있다. 그러나 **서버에 남은 옛 태그·별칭은 확장 목록과 무관하게 직접 호출될 수 있으므로**, 운영 전환 때 서버 측에서 태그를 제거하고 모든 공개 URL을 검증한다.
6. 롤백은 Firestore 모드 리비전끼리 한다. 차단 이후 env 허용 목록 리비전으로 되돌리면 차단 계정이 재허용될 수 있다.

## 필수 인수 테스트와 검증 상태

| 항목 | 검증 방법 | 이번 상태 |
|---|---|---|
| 토큰 없음·위조·다른 프로젝트·만료·취소·비활성 | `test_auth.py`, `test_access_api.py` (검증기 대역) | 통과 |
| Google 제공자·이메일 인증 유지, 신원/허용목록 거절 구분 | 같은 파일 | 통과 |
| 일반 사용자의 관리 API 403, `role`/`decided_by`/`email` 주입 422 | `test_access_api.py` | 통과 |
| 다른 UID·이메일로 신청, 자기 승인 불가 | `test_access_api.py` | 통과 |
| 반복·거절·차단 후 신청이 상태를 덮지 않음 | `test_access_api.py`, `test_access_policy.py` | 통과 |
| pending 로그인 유지, 추천·EQ·매칭·미리듣기 403 | 백엔드 HTTP 테스트, 확장 `authAccess`·`background`·`sidepanelAccess` | 통과 |
| 승인 후 재로그인 없이 상태 확인, 차단 후 다음 요청 403 | 같은 테스트 | 통과 |
| 동시 처리·재전송이 중복 감사·덮어쓰기를 만들지 않음 | 정책 함수 + 메모리 저장소(잠금), 가짜 Firestore(재시도·낙관적 충돌 모사), **에뮬레이터 테스트** | 앞의 둘 통과, 에뮬레이터는 미실행 |
| DB 장애 503, legacy credential·env 허용 목록 우회 불가 | `test_access_api.py` | 통과 |
| 직접 Firestore 클라이언트 읽기·쓰기 거부 | 규칙 정적 테스트 + **에뮬레이터 규칙 테스트** | 정적 통과, 에뮬레이터 미실행 |
| 서버 IAM 경계 | 운영 확인 | 미검증 |
| 계정 변경·로그아웃·worker 재시작 시 이전 권한·결과·작업 재사용 안 함 | 확장 단위 테스트(가짜 DOM 포함) | 통과 |
| 미리듣기 Blob 해제, EQ 중지, 취소된 요청의 늦은 응답 무시 | `previewPlayer`, `offscreen`, `sidepanelAccess` 테스트 | 통과 |
| Chrome 실제 렌더링 E2E | Playwright (`e2e/`, 시나리오 갱신) | 환경 오류로 실행 불가 |
| 실제 운영자 관리자 로그인, 비관리자 신청·승인·차단 | 운영 확인 | 미검증 |

## 비용 예상

별도 관리자 서버, 실시간 구독, 메일·푸시 발송은 없다. 기능 요청과 `/auth/me`는 문서 읽기 1회, 신규 신청은 읽기 2회 + 쓰기 2회(사용자·일일 카운터), 결정은 읽기 2회 + 쓰기 2회(상태·감사), 목록은 반환 문서 수 + 1회 읽기다. Firebase 검증 호출과 서버 처리 비용은 별개다.

Firestore 무료 한도는 DB 하나에 읽기 하루 50,000회, 쓰기 하루 20,000회, 저장 공간 1 GiB다. 인스턴스 로컬 분당 제한만으로는 하루 비용 상한을 보장하지 못하므로 `--max-instances` 고정, aggregate 값 조정, 예산 알림이 필요하다. 기존 Cloud Run 사용량·운영 DB 상태는 미확인이다. [공식 요금](https://firebase.google.com/docs/firestore/pricing).

## 구현 위치

| 영역 | 파일 |
|---|---|
| 승인 정책·Firestore 저장소 | `backend/app/services/access.py` |
| 신원 확인·승인 의존성·오류 계약 | `backend/app/services/auth.py`, `backend/app/routers/auth.py` |
| 신청·관리 API, 본문 스키마 | `backend/app/routers/access.py`, `backend/app/schemas/access.py` |
| 설정·기동 검증, 연결 | `backend/app/config/__init__.py`, `backend/main.py` |
| 본문 크기 제한 | `backend/app/utils/body_limit.py` |
| 미리듣기 승인·바이트 상한 | `backend/preview.py` |
| 부트스트랩 | `backend/scripts/bootstrap_access.py` |
| 규칙·색인·에뮬레이터 설정 | `deployment/firestore/` |
| 확장 세션/승인 분리 | `extension/scripts/authWorker.entry.js`(→ `dist/authWorker.js`), `background.js`, `scripts/authClient.js` |
| 승인 화면·관리 화면·미리듣기 | `extension/sidepanel.*`, `scripts/accessView.js`, `scripts/accessAdmin.js`, `scripts/previewPlayer.js` |
| EQ 승인 상실 처리 | `extension/scripts/eqProvider.js`, `extension/offscreen.js` |
