// Shared by the booking page and the ops console.
// Every value from the API is put in the page as text, never as HTML.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export async function api(method, path, { body, token, headers } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(headers || {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "offline", "No connection. Check your network and try again.");
  }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (response.ok) return data;
  if (data && data.error) throw new ApiError(response.status, data.error.code, data.error.message);
  // FastAPI's own validation errors: say which field.
  const first = data && Array.isArray(data.detail) ? data.detail[0] : null;
  const where = first ? first.loc.filter((p) => p !== "body").join(".") : "";
  throw new ApiError(
    response.status,
    "invalid",
    first ? `${where}: ${first.msg}` : `Request failed (${response.status})`,
  );
}

export function money(amount, locale) {
  const major = amount.amount_minor / 100;
  return `${major.toLocaleString(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${amount.currency}`;
}

// "1200.50" -> 120050 without going through a float.
export function toMinor(text) {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(text.trim());
  if (!match) return null;
  return Number(match[1]) * 100 + Number((match[2] || "").padEnd(2, "0"));
}

const ADDIS = "Africa/Addis_Ababa";

export function addisTime(iso, locale) {
  return new Date(iso).toLocaleString(locale, { timeZone: ADDIS, dateStyle: "medium", timeStyle: "short" });
}

// A datetime-local value typed as Addis Ababa time (UTC+3, no daylight saving) -> UTC ISO.
export function addisInputToIso(value) {
  return new Date(`${value}:00+03:00`).toISOString();
}

export function ref(id) {
  return id.slice(-6).toUpperCase();
}

export function field(label, input) {
  return h("div", {}, h("label", { for: input.id }, label), input);
}

// Replace an element's content. Unlike replaceChildren, this accepts nested arrays
// and skips null and false, the same way h() does.
export function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}
