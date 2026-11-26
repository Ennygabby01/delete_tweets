export class PublicError extends Error {}
export const publicMessage = (error) =>
  error instanceof PublicError
    ? error.message
    : "Something went wrong. Please try again.";

let csrf = "";
let sessionRevision = 0;
let lostSession = () => {};

export function setSession(value) {
  const next = value?.csrf || "";
  if (next !== csrf) sessionRevision++;
  csrf = next;
}
export function onSessionLost(callback) {
  lostSession = callback;
}

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const revision = sessionRevision;
  const headers = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  if (body !== undefined)
    headers["Content-Type"] = raw
      ? "application/octet-stream"
      : "application/json";
  let response;
  try {
    response = await fetch(`/v1${path}`, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers,
      body: body === undefined ? undefined : raw ? body : JSON.stringify(body),
    });
  } catch {
    throw new PublicError(
      "The app is unreachable. Check your connection and try again.",
    );
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new PublicError(
      "The server returned an unreadable response. Try again.",
    );
  }
  if (revision !== sessionRevision)
    throw new PublicError("Your session changed. Please try again.");
  if (!response.ok) {
    if (response.status >= 500)
      throw new PublicError(
        "The app could not complete this request. Please try again.",
      );
    if (response.status === 401) lostSession();
    throw new PublicError(
      typeof data.detail === "string"
        ? data.detail
        : "The request could not be completed.",
    );
  }
  return data;
}
