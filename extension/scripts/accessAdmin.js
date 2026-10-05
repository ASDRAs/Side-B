// Administrator account approval client. Showing this UI is only a hint from
// /auth/me (can_manage_access); the server re-checks the administrator list on
// every request, including replays of the same operation ID.

export const ADMIN_PAGE_SIZE = 25;
export const ADMIN_TABS = Object.freeze([
  { status: "pending", label: "대기" },
  { status: "approved", label: "승인" },
  { status: "rejected", label: "거절" },
  { status: "blocked", label: "차단" },
]);

const ACTIONS = Object.freeze({
  pending: [{ action: "approve", label: "승인" }, { action: "reject", label: "거절" }],
  approved: [{ action: "block", label: "차단" }],
  blocked: [{ action: "unblock", label: "차단 해제" }],
  rejected: [{ action: "reopen", label: "재심사" }],
});

const DONE_TEXT = Object.freeze({
  approve: "승인했습니다.",
  reject: "거절했습니다.",
  block: "차단했습니다.",
  unblock: "차단을 해제했습니다.",
  reopen: "재심사 대기로 되돌렸습니다.",
});

// Administrator accounts are changed only through server settings.
export function actionsFor(item) {
  if (!item || item.is_admin) return [];
  return ACTIONS[item.status] || [];
}

export function decisionDoneText(action) {
  return DONE_TEXT[action] || "처리했습니다.";
}

export class AdminApiError extends Error {
  constructor(message, { status = 0, code = null } = {}) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
    this.code = code;
  }
}

async function adminRequest(fetchImpl, url, init) {
  let response;
  try {
    response = await fetchImpl(url, init);
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new AdminApiError(error?.message || "서버에 연결하지 못했습니다.");
  }
  let payload = null;
  try { payload = await response.json(); } catch { payload = null; }
  if (!response.ok) {
    const detail = payload?.detail && typeof payload.detail === "object" && !Array.isArray(payload.detail)
      ? payload.detail : {};
    throw new AdminApiError(
      typeof detail.message === "string" && detail.message ? detail.message : `HTTP ${response.status}`,
      { status: response.status, code: typeof detail.code === "string" ? detail.code : null },
    );
  }
  return payload;
}

function validItem(item) {
  return item && typeof item.uid === "string" && item.uid &&
    ["pending", "approved", "rejected", "blocked"].includes(item.status) &&
    Number.isInteger(item.revision);
}

export async function listAccessUsers(fetchImpl, apiBaseUrl, { status, cursor = null, limit = ADMIN_PAGE_SIZE, signal } = {}) {
  const params = new URLSearchParams({ status, limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const payload = await adminRequest(fetchImpl, `${apiBaseUrl}/admin/access-users?${params}`, { method: "GET", signal });
  if (!Array.isArray(payload?.items) || !payload.items.every(validItem)) {
    throw new AdminApiError("계정 목록 응답 형식이 올바르지 않습니다.");
  }
  return {
    items: payload.items,
    nextCursor: typeof payload.next_cursor === "string" && payload.next_cursor ? payload.next_cursor : null,
  };
}

export async function decideAccess(fetchImpl, apiBaseUrl, { uid, action, expectedRevision, operationId, signal }) {
  // Only the allowlisted action, revision and idempotency key are sent. The
  // server derives the administrator, timestamps and target state itself.
  return adminRequest(
    fetchImpl,
    `${apiBaseUrl}/admin/access-users/${encodeURIComponent(uid)}/decision`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, expected_revision: expectedRevision, operation_id: operationId }),
      signal,
    },
  );
}

// How the panel reacts to a failed administrator request.
export function adminErrorOutcome(error) {
  const status = error?.status || 0;
  const code = error?.code || null;
  if (status === 409 || code === "access_user_not_found") {
    return { message: "이미 처리되었거나 다른 관리자가 먼저 변경했습니다. 최신 목록을 불러옵니다.",
      reload: true, revoke: false, retry: false };
  }
  if (code === "admin_required" || code === "access_management_disabled" || status === 401) {
    return { message: "관리자 권한을 확인하지 못했습니다. 계정 관리 화면을 닫습니다.",
      reload: false, revoke: true, retry: false };
  }
  if (code === "access_admin_target_protected") {
    return { message: error.message, reload: false, revoke: false, retry: false };
  }
  if (status === 422) {
    return { message: `요청이 거부되었습니다: ${error.message}`, reload: false, revoke: false, retry: false };
  }
  return {
    message: `처리하지 못했습니다: ${error?.message || "알 수 없는 오류"}. 다시 시도할 수 있습니다.`,
    reload: false, revoke: false, retry: true,
  };
}
