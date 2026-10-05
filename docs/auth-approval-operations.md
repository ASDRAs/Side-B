# Side-B 계정 승인 운영 절차

작성일: 2026-10-05 (한국 시간). 이 문서는 구현된 코드에 맞춘 운영 절차다. 이번 작업에서는 Firestore 생성·규칙 배포·IAM 부여·관리자 지정·부트스트랩 실행·Cloud Run 배포·태그 변경을 **하나도 수행하지 않았다.** 아래 명령은 운영자가 직접 확인하고 실행할 절차이며, 실행 결과는 검증되지 않았다.

설계와 권한 원칙은 [auth-approval-design.md](auth-approval-design.md)에 있다.

## 1. 승인 저장소 모드와 기동 조건

`SIDE_B_ACCESS_STORE`가 승인 근거를 고른다.

| 값 | 신원 확인 | 사용 승인 근거 | 관리 API |
|---|---|---|---|
| `env` (기본값) | `SIDE_B_AUTH_MODE`의 legacy/dual/firebase 동작 그대로 | `FIREBASE_ALLOWED_UIDS`/`FIREBASE_ALLOWED_EMAILS` 또는 legacy 공유 토큰 | 없음 (404 `access_management_disabled`) |
| `firestore` | Firebase ID 토큰만 (서명·프로젝트·만료·취소·Google 제공자·인증된 이메일) | Firestore `access_users/{uid}.status == "approved"` | `/access/request`, `/admin/access-users*` |

`firestore`일 때 다음 중 하나라도 해당하면 서버는 기동하지 않는다(`AccessConfigurationError`).

- `SIDE_B_AUTH_MODE`가 `firebase`가 아님 (legacy·dual 금지)
- `FIREBASE_PROJECT_ID`가 비어 있음
- `SIDE_B_ADMIN_UIDS`가 비어 있거나 문서 ID로 쓸 수 없는 UID가 있음
- `ALLOW_UNAUTHENTICATED_RECOMMEND=true`

`SIDE_B_ACCESS_TOKEN`·`FIREBASE_ALLOWED_*`가 남아 있으면 경고만 남기고 **무시**한다. Firestore 모드의 코드 경로는 환경변수 허용 목록이나 공유 토큰을 승인 근거로 쓰지 않으며, DB 장애는 503으로 실패한다(대체 허용 없음). 그래도 혼동과 롤백 사고를 막기 위해 운영 전환 시 제거한다.

### 운영 환경변수 (Firestore 모드)

```text
SIDE_B_AUTH_MODE=firebase
SIDE_B_ACCESS_STORE=firestore
FIREBASE_PROJECT_ID=<Firebase 프로젝트 ID>
SIDE_B_ADMIN_UIDS=<운영자가 Firebase Authentication에서 확인한 UID>[,<UID>...]
SIDE_B_FIRESTORE_PROJECT_ID=            # 비우면 FIREBASE_PROJECT_ID
SIDE_B_FIRESTORE_DATABASE=(default)
SIDE_B_ACCESS_STORE_TIMEOUT_SECONDS=8   # DB 호출 하나의 상한
SIDE_B_ACCESS_STORE_CONCURRENCY=8       # 인스턴스당 동시 DB 호출 상한
ACCESS_REQUEST_DAILY_LIMIT=50           # 전체 신규 신청 생성/일(UTC), Firestore 카운터로 전역 적용
# 요청 제한(분당, 인스턴스 로컬). *_AGGREGATE_*는 인스턴스 전체 합계.
ACCESS_STATUS_REQUESTS_PER_MINUTE=20    ACCESS_STATUS_AGGREGATE_REQUESTS_PER_MINUTE=300   # GET /auth/me
ACCESS_LOOKUP_REQUESTS_PER_MINUTE=60    ACCESS_LOOKUP_AGGREGATE_REQUESTS_PER_MINUTE=600   # 기능 호출 전 승인 조회
ACCESS_REQUEST_REQUESTS_PER_MINUTE=3    ACCESS_REQUEST_AGGREGATE_REQUESTS_PER_MINUTE=30   # POST /access/request
ADMIN_READ_REQUESTS_PER_MINUTE=30       ADMIN_READ_AGGREGATE_REQUESTS_PER_MINUTE=120
ADMIN_WRITE_REQUESTS_PER_MINUTE=30      ADMIN_WRITE_AGGREGATE_REQUESTS_PER_MINUTE=120
PREVIEW_REQUESTS_PER_MINUTE=30          PREVIEW_AGGREGATE_REQUESTS_PER_MINUTE=300
# 제거: SIDE_B_ACCESS_TOKEN, YOUTUBE_EXPORT_TOKEN, FIREBASE_ALLOWED_UIDS, FIREBASE_ALLOWED_EMAILS
```

(위 블록은 설명용 표기다. 실제 `.env`/`--update-env-vars`에는 한 줄에 하나씩 넣는다.)

최초 관리자 후보는 사용자가 지정한 `asdra030522@gmail.com` 계정이다. 이 계정의 Firebase UID는 확인되지 않았으므로 저장소·문서·코드 어디에도 UID를 넣지 않았다. 운영자가 Firebase 콘솔 → Authentication → 사용자 목록에서 해당 이메일의 UID를 확인해 설정한다. 관리자 추가·제거는 이 환경변수를 바꾸는 배포 작업이며 API로는 할 수 없다.

## 2. Firestore 데이터와 규칙

| 경로 | 내용 | 작성 주체 |
|---|---|---|
| `access_users/{uid}` | `uid, email, display_name, status(pending/approved/rejected/blocked), revision, requested_at, updated_at, decided_at, decided_by` | 백엔드만. 이메일·표시 이름은 검증된 토큰에서 복사 |
| `access_audit/{관리자UID}:{operation_id}` | `operation_id, actor_uid, target_uid, action, expected_revision, previous_status, new_status, previous_revision, new_revision, created_at` | 백엔드만. 토큰·헤더·이메일 없음 |
| `access_request_quota/{YYYY-MM-DD}` | `date, count, updated_at` (UTC 날짜별 신규 신청 수) | 백엔드만 |

문서가 없는 UID는 `unregistered`다. 시각은 백엔드 서버 시계(UTC)로 기록한다. 상태 변경과 감사 기록은 하나의 트랜잭션으로 커밋하고, 감사 문서는 결정적 ID에 `create()`로 쓰므로 재시도·재전송이 감사 기록을 두 번 만들 수 없다.

UID는 ASCII로 제한하지 않으며 최대 128자(UTF-8 최대 512바이트)다. 관리자 목록의 불투명 커서는 최대 766자다(UID와 상태·UTC 시각을 담은 JSON 최대 574바이트의 base64url). 인코더·디코더·HTTP 검증이 같은 상한을 사용하며 기존 짧은 커서도 그대로 읽는다. Firestore 문서 스키마·색인 변경은 없다.

규칙·인덱스 파일은 `deployment/firestore/`에 있다.

- `firestore.rules`: 모든 문서의 클라이언트 읽기·쓰기 거부. 서버 SDK는 규칙을 우회하므로 권한 검사는 백엔드 API가 한다.
- `firestore.indexes.json`: 관리자 목록 쿼리(`status ==` + `requested_at` 오름차순)용 복합 색인. **운영 DB에 이 색인이 없으면 목록 API가 503을 반환한다.** 에뮬레이터는 색인이 필요 없다.

운영자 배포 예시(이번에 실행하지 않음, Firebase CLI 필요):

```powershell
cd deployment/firestore
firebase deploy --only firestore:rules,firestore:indexes --project <PROJECT_ID>
```

기존 DB 위치·존재 여부는 확인되지 않았다. 기존 Firestore(Native) DB가 있으면 새 DB를 만들지 말고 컬렉션만 추가한다. 다른 앱이 같은 DB에서 클라이언트 규칙을 쓰고 있다면 deny-all 규칙을 그대로 덮어쓰면 그 앱이 깨진다. 이 경우 기존 규칙에 위 세 컬렉션의 거부 규칙만 병합한다.

## 3. IAM

- 백엔드 Cloud Run 런타임 서비스 계정: Firestore 프로젝트에 `roles/datastore.user`. 키 파일은 쓰지 않는다(Application Default Credentials). 계정 이름은 `gcloud run services describe side-b-backend --region asia-northeast3 --format="value(spec.template.spec.serviceAccountName)"`로 확인한다.
- 같은 계정은 이미 `verify_id_token(check_revoked=True)`를 위해 Firebase Authentication 사용자 조회 권한이 필요하다. 현재 부여 상태는 확인되지 않았다.
- 부트스트랩 실행자(운영자): 실행하는 동안만 `roles/firebaseauth.viewer`와 Firestore 쓰기 권한(`roles/datastore.user`).
- 확장 프로그램과 사용자 계정에는 Firestore 권한을 주지 않는다.

## 4. 기존 사용자·관리자 이관 (부트스트랩)

`backend/scripts/bootstrap_access.py`. 이번 작업에서는 `--help`와 가짜 Firebase/Firestore 단위 테스트만 실행했다.

1. 현재 허용 목록을 확보한다: 현재 트래픽 리비전의 `FIREBASE_ALLOWED_UIDS`/`FIREBASE_ALLOWED_EMAILS` 값.
2. 운영자 ADC로 로그인한다: `gcloud auth application-default login`.
3. 드라이런(기본값, 쓰기 없음):

   ```powershell
   cd backend
   poetry run python scripts/bootstrap_access.py --project <PROJECT_ID> `
     --admin-uid <ADMIN_UID> --approved-uid <UID> --approved-email <EMAIL>
   ```

4. 출력 JSON의 `would_create`/`exists`를 검토한 뒤 같은 명령에 `--apply`를 붙여 실행한다.

동작:

- 모든 입력을 Firebase Authentication에서 조회한다. 존재하지 않거나, 비활성화되었거나, Google 로그인 제공자가 아니거나, 이메일이 인증되지 않은 계정이 하나라도 있으면 아무것도 쓰지 않고 종료 코드 2로 끝난다.
- 이메일 입력은 Firebase UID로 바꿔 저장한다(이메일 매핑은 Firebase 기록 기준).
- 관리자 UID와 기존 허용 계정을 `approved`, `revision=1`, `decided_by="bootstrap-migration"`으로 만든다. 감사 기록 `access_audit/bootstrap-migration:{uuid}`도 같은 트랜잭션에서 만든다.
- 이미 문서가 있으면 덮어쓰지 않고 `exists`와 현재 상태를 보고한다. 차단·거절 상태가 보존된다.

## 5. 배포 순서

1. 확인: 운영 Firebase UID, 기존 허용 목록, Firestore DB 존재·위치, 백엔드 서비스 계정, 현재 트래픽·태그(`gcloud run services describe side-b-backend --region asia-northeast3 --format="yaml(status.traffic,status.url,status.address)"`).
2. 규칙·색인 배포, IAM 부여.
3. 부트스트랩 드라이런 → `--apply`.
4. 새 백엔드 리비전을 `--no-traffic --tag <새태그>`로 배포하면서 1절의 환경변수를 설정하고 옛 변수를 제거한다(`--remove-env-vars` 또는 시크릿이면 `--remove-secrets`). `deployment/deploy-clap.ps1`은 추론 관련 변수만 갱신하므로 승인 전환을 대신하지 않는다.
5. 후보 태그 URL에서 확인: `/auth/config`가 `mode=firebase`; 토큰 없는 `/auth/me`·`/preview/stream`·`/admin/access-users`가 401; 관리자 계정 로그인 후 목록 조회; 미등록 계정의 신청 → pending; 승인 → 기능 사용; 차단 → 다음 기능 요청 403; 일반 계정의 관리자 API 403.
6. 새 확장 배포(아래 7절). 옛 확장은 pending 화면과 Blob 미리듣기를 처리하지 못한다.
7. 트래픽 이동 후 같은 검증을 일반 URL에서 반복한다.

## 6. 옛 공개 태그·별칭 정리 (운영 전환 필수)

Cloud Run 태그 URL은 트래픽 비율과 관계없이 그 태그가 가리키는 리비전으로 직접 연결된다(Google 문서: `gcloud run services update-traffic`). 따라서 dual 모드·환경변수 허용 목록을 가진 옛 리비전의 태그(예: `auth-20260915-011056---side-b-backend-...`)가 남아 있으면, 차단된 사용자나 공유 토큰 보유자가 그 URL을 직접 호출해 승인 정책을 우회할 수 있다. **확장의 신뢰 목록에서 주소를 지우는 것만으로는 서버 접근이 막히지 않는다.**

1. 모든 태그·리비전·URL 나열: `gcloud run services describe side-b-backend --region asia-northeast3 --format="yaml(status.traffic)"`, `gcloud run revisions list --service side-b-backend --region asia-northeast3`.
2. 옛 정책 리비전을 가리키는 태그 제거: `gcloud run services update-traffic side-b-backend --region asia-northeast3 --remove-tags <TAG>[,<TAG>]`.
3. 일반 URL 두 개(`side-b-backend-7hmhv6htsa-du.a.run.app`, `side-b-backend-1073342688292.asia-northeast3.run.app`)가 같은 서비스의 URL인지, 다른 서비스·리전·커스텀 도메인 매핑·로드밸런서 별칭이 옛 정책 리비전을 서비스하지 않는지 확인한다. 이번 작업에서는 확인하지 못했다.
4. 차단 계정·토큰 없는 요청으로 남은 모든 공개 URL에서 기능 API가 403/401인지 확인한다.

확장 코드는 현재 기본 주소(`side-b-backend-1073342688292.asia-northeast3.run.app`)와 옛 기본값(일반 URL·`auth-20260915-011056` 태그 URL)의 저장값 이관을 그대로 유지한다. 옛 주소를 `auth.config.js`/`manifest.json` 신뢰 목록에서 빼는 것은 서버 측 정리를 끝낸 뒤의 별도 확장 릴리스로 한다.

## 7. 확장 동작 요약

- 로그인 세션과 승인 상태는 별개다. pending·rejected·blocked·unregistered 계정은 로그인 상태로 승인 화면을 보고 상태 확인·신청(미등록만)·로그아웃만 할 수 있다. 승인 확인은 수동 새로고침이며 폴링하지 않는다.
- 서버가 `can_manage_access=true`를 준 계정만 설정에 "계정 관리"가 보인다. 대기·승인·거절·차단 탭(25개씩, 커서), 승인·거절·차단·차단 해제·재심사. 실패한 결정은 같은 operation ID로 재시도하고, 409는 최신 목록을 다시 불러온다.
- 현재 계정·서버·세대의 기능 API가 403 `access_not_approved`를 주면 즉시 기능 권한을 회수하고 진행 중인 요청·결과·캐시·Blob을 정리하며 EQ를 멈춘 뒤 승인 상태를 다시 읽는다. 후속 조회가 429·503·네트워크 오류로 실패해도 이전 승인을 복원하지 않는다. 관리자 API의 403 `admin_required`는 관리자 메뉴·목록·재시도 상태만 정리하고 기능 승인과 Firebase 로그인은 유지한다. 다른 계정이나 이전 worker의 늦은 거부 응답은 현재 세션에 적용하지 않는다. 신원 거절(`auth_identity_unverified`)은 로그아웃된다.
- 미리듣기는 인증 fetch로 최대 8 MiB를 받아 Blob URL로 재생하며, 곡 전환·로그아웃·승인 상실 시 요청을 취소하고 URL을 해제한다. 토큰은 URL에 넣지 않는다. 이미 만든 YouTube 플레이리스트나 내려받은 데이터는 소급 삭제하지 않는다.

## 8. 요청 제한과 비용 상한의 한계

- 모든 분당 제한은 **인스턴스 로컬 메모리**다. 전체 상한은 대략 `인스턴스당 aggregate × 실제 인스턴스 수`이며, 인스턴스가 재시작되면 창이 초기화된다. 백엔드의 Cloud Run `--max-instances` 값은 `deployment/cloudrun.json`에 고정되어 있지 않고 현재 운영 값도 확인되지 않았다. 운영 전환 시 명시적으로 설정해야 한다.
- 신규 신청 생성만은 `ACCESS_REQUEST_DAILY_LIMIT`가 Firestore 카운터로 전 인스턴스에 걸쳐 적용된다.
- Firestore 작업 수: `/auth/me` 1 읽기, 기능·미리듣기 요청 1 읽기, 신규 신청 2 읽기 + 2 쓰기(사용자·카운터), 반복 신청 1 읽기, 결정 2 읽기 + 2 쓰기(상태·감사), 재전송 2 읽기, 목록은 반환 문서 수 + 1 읽기(최소 1).
- 기본값에서 일반 사용자 경로의 읽기 상한은 인스턴스당 분당 약 900회(`/auth/me` 300 + 기능·미리듣기 승인 조회 600)이고, 여기에 신청(분당 30회 × 최대 2 읽기)과 관리자 목록(분당 120회 × 최대 51 읽기)이 더해진다. 이 값은 무료 한도(일 50,000 읽기)를 악용 시 넘을 수 있다. 분당 제한은 남용 차단용이지 무료 한도 보장이 아니다. Firestore에는 하드 지출 상한이 없으므로 `--max-instances`를 작게 고정하고, 필요하면 aggregate 값을 낮추고, GCP 예산 알림을 설정한다.
- 인증 사용자 버킷 상한(`AUTHENTICATED_USER_BUCKET_LIMIT`, 기본 1000)은 `(UID, 기능)` 쌍 기준이다. 한 사용자가 여러 버킷을 쓰므로 동시 활성 사용자가 수백 명을 넘으면 새 사용자가 일시적으로 429를 받을 수 있다.

## 9. 롤백

- 승인·차단 결과는 Firestore에 남는다. 백엔드 롤백은 **Firestore 모드 리비전끼리** 한다.
- 차단 이후 env 허용 목록 리비전으로 되돌리면 차단 계정이 다시 허용될 수 있다. 불가피하면 Firestore의 `approved` 문서만으로 허용 목록을 다시 만들고 blocked·rejected를 빼서 배포한다.
- 롤백이 Firestore 문서를 지우지 않으며, 감사 기록도 유지된다.

## 10. Firestore 에뮬레이터 통합 테스트

실제 트랜잭션·동시성·규칙 거부는 `backend/tests/integration/test_firestore_emulator.py`가 검사한다. `FIRESTORE_EMULATOR_HOST`가 없으면 건너뛴다(=미검증).

```powershell
cd deployment/firestore
firebase emulators:start --only firestore --project demo-side-b-access   # Java와 Firebase CLI 필요
# 다른 셸
cd backend
$env:FIRESTORE_EMULATOR_HOST = "127.0.0.1:8085"
poetry run pytest tests/integration -q
```

이번 작업 환경에는 Java·Firebase CLI·gcloud가 없고 Docker 데몬도 실행 중이 아니어서 이 6개 테스트를 실행하지 못했다.

## 11. 현재 운영 로그인 거절 원인

기존 운영 확장에서 보인 "승인되지 않은 계정" 거절의 원인은 **확정되지 않았다.** 현재 코드의 `/auth/me` 403은 Google 제공자·이메일 인증 실패와 환경변수 허용 목록 미포함 두 경우에서 나온다. 이번 구현은 두 경우를 `auth_identity_unverified`와 `auth_account_denied` 코드로 나누고 확장이 코드를 표시하므로, 새 서버·확장에서는 구분할 수 있다. 기존 운영 리비전의 원인은 그 리비전의 403 응답 본문, Cloud Run 로그 또는 환경변수 확인으로만 확정할 수 있다. 승인 기능 설계·구현이 완료되었다는 사실이 그 거절이 해결되었음을 뜻하지 않는다.
