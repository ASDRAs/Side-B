// Access approval is separate from the Google session. These helpers decide
// what the panel shows; the server still authorizes every request.

export const ACCESS_STATUS_LABELS = Object.freeze({
  unregistered: "사용 신청 전",
  pending: "승인 대기",
  approved: "승인됨",
  rejected: "거절됨",
  blocked: "차단됨",
  unavailable: "확인 불가",
});

const GATE_TEXT = Object.freeze({
  unregistered: {
    title: "사용 신청이 필요합니다",
    copy: "Google 로그인은 완료되었습니다. 관리자가 승인하면 Side-B를 사용할 수 있습니다.",
  },
  pending: {
    title: "관리자 승인 대기 중",
    copy: "신청이 접수되었습니다. 승인된 뒤 '승인 상태 확인'을 누르면 바로 사용할 수 있습니다.",
  },
  rejected: {
    title: "사용 신청이 거절되었습니다",
    copy: "다시 신청해도 상태가 바뀌지 않습니다. 필요하면 관리자에게 재심사를 요청하세요.",
  },
  blocked: {
    title: "이용이 차단되었습니다",
    copy: "이 계정은 Side-B 기능을 사용할 수 없습니다. 관리자에게 문의하세요.",
  },
  unavailable: {
    title: "승인 상태를 확인하지 못했습니다",
    copy: "서버가 승인 상태를 확인하지 못했습니다. 잠시 후 다시 확인하세요.",
  },
});

export function isManagedMode(state) {
  return state?.mode === "firebase" || state?.mode === "dual";
}

// A signed-in session whose approval is not confirmed keeps the account but
// shows the access screen instead of the features.
export function isAccessGated(state) {
  return isManagedMode(state) && state?.status === "signed_in" && state?.access?.status !== "approved";
}

export function accessGateView(access) {
  const status = Object.hasOwn(GATE_TEXT, access?.status) ? access.status : "unavailable";
  return {
    status,
    ...GATE_TEXT[status],
    canRequest: status === "unregistered" && access?.store === "firestore",
    showRequestedAt: Boolean(access?.requestedAt) && ["pending", "rejected", "blocked"].includes(status),
  };
}

// A feature 403 that means "this account is not approved" (session stays).
// Other 403s are identity or allowlist rejections and are reported as before.
export function accessDenialStatus(status, payload) {
  const detail = payload?.detail;
  if (status !== 403 || detail?.code !== "access_not_approved") return null;
  return typeof detail.access_status === "string" ? detail.access_status : "unavailable";
}

export function formatTimestamp(value, locale = "ko-KR") {
  if (typeof value !== "string" || !value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}

export class AccessDeniedError extends Error {
  constructor(status, message = "관리자 승인 후 사용할 수 있습니다.") {
    super(message);
    this.name = "AccessDeniedError";
    this.accessStatus = status;
  }
}
