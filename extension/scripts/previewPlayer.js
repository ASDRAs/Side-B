// `<audio src>` cannot carry an Authorization header, and tokens must never go
// into a URL. The panel fetches the preview with its normal credential, keeps
// at most MAX_PREVIEW_BYTES, and plays it from a revocable Blob URL.

export const MAX_PREVIEW_BYTES = 8 * 1024 * 1024;

export class PreviewLoadError extends Error {
  constructor(message, { status = 0, payload = null } = {}) {
    super(message);
    this.name = "PreviewLoadError";
    this.status = status;
    this.payload = payload;
  }
}

export async function readLimitedAudio(response, { maxBytes = MAX_PREVIEW_BYTES } = {}) {
  const type = String(response.headers.get("Content-Type") || "").split(";")[0].trim().toLowerCase();
  if (!type.startsWith("audio/")) throw new PreviewLoadError("미리 듣기 응답이 오디오가 아닙니다.");
  const declared = Number(response.headers.get("Content-Length"));
  if (Number.isFinite(declared) && declared > maxBytes) {
    await response.body?.cancel().catch(() => {});
    throw new PreviewLoadError("미리 듣기 파일이 너무 큽니다.");
  }
  const chunks = [];
  let size = 0;
  if (response.body?.getReader) {
    const reader = response.body.getReader();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) {
          await reader.cancel().catch(() => {});
          throw new PreviewLoadError("미리 듣기 파일이 너무 큽니다.");
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock?.();
    }
  } else {
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes) throw new PreviewLoadError("미리 듣기 파일이 너무 큽니다.");
    chunks.push(buffer);
  }
  return new Blob(chunks, { type });
}

// One preview at a time. Loading another, cancelling or resetting aborts the
// fetch, ignores its late result and revokes the previous Blob URL.
export function createPreviewSession({
  fetchPreview,
  createObjectURL = (blob) => URL.createObjectURL(blob),
  revokeObjectURL = (url) => URL.revokeObjectURL(url),
  maxBytes = MAX_PREVIEW_BYTES,
} = {}) {
  let generation = 0;
  let controller = null;
  let objectUrl = null;

  function cancel() {
    generation += 1;
    controller?.abort();
    controller = null;
    if (objectUrl) {
      revokeObjectURL(objectUrl);
      objectUrl = null;
    }
  }

  async function load(url) {
    cancel();
    const current = generation;
    const request = new AbortController();
    controller = request;
    const stale = () => current !== generation;
    const abortError = () => new DOMException("Preview load was cancelled", "AbortError");
    try {
      const response = await fetchPreview(url, { signal: request.signal });
      if (stale()) throw abortError();
      if (!response.ok) {
        let payload = null;
        try { payload = await response.json(); } catch { payload = null; }
        throw new PreviewLoadError("미리 듣기를 불러오지 못했습니다.", { status: response.status, payload });
      }
      const blob = await readLimitedAudio(response, { maxBytes });
      if (stale()) throw abortError();
      objectUrl = createObjectURL(blob);
      return objectUrl;
    } catch (error) {
      if (stale() || request.signal.aborted) throw abortError();
      throw error;
    } finally {
      if (controller === request) controller = null;
    }
  }

  return {
    load,
    cancel,
    get loading() { return controller !== null; },
    get objectUrl() { return objectUrl; },
  };
}
