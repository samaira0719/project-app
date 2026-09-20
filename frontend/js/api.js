/** Thin fetch wrapper with JWT bearer auth. */

const TOKEN_KEY = "dw_token";

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}
export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch { /* private mode */ }
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail);
    this.status = status;
  }
}

async function request(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (response.status === 204) return null;
  let data = null;
  try { data = await response.json(); } catch { /* no body */ }

  if (!response.ok) {
    let detail = data?.detail ?? `Request failed (${response.status})`;
    if (Array.isArray(detail)) detail = detail.map((d) => d.msg).join("; ");
    throw new ApiError(response.status, detail);
  }
  return data;
}

export const api = {
  register: (payload) => request("POST", "/api/auth/register", payload),
  login: (payload) => request("POST", "/api/auth/login", payload),
  me: () => request("GET", "/api/auth/me"),

  getSurveySpec: () => request("GET", "/api/survey/spec"),
  getSurvey: () => request("GET", "/api/survey"),
  saveSurvey: (answers) => request("PUT", "/api/survey", { answers }),

  createTask: (payload) => request("POST", "/api/tasks", payload),
  listTasks: (status) =>
    request("GET", `/api/tasks${status ? `?status_filter=${status}` : ""}`),
  prioritized: () => request("GET", "/api/tasks/prioritized"),
  completeTask: (id) => request("PATCH", `/api/tasks/${id}/complete`),
  // quadrant: "do" | "schedule" | "delegate" | "eliminate", or null to reset
  setQuadrant: (id, quadrant) =>
    request("PATCH", `/api/tasks/${id}/quadrant`, { quadrant }),
  reopenTask: (id) => request("PATCH", `/api/tasks/${id}/reopen`),
  deleteTask: (id) => request("DELETE", `/api/tasks/${id}`),
  stats: () => request("GET", "/api/tasks/stats"),

  decisionTemplate: (category) =>
    request("GET", `/api/tasks/decision-template/${encodeURIComponent(category)}`),
  getDecision: (id) => request("GET", `/api/tasks/${id}/decision`),
  decideTask: (id, payload) => request("POST", `/api/tasks/${id}/decide`, payload),
  decideTaskAuto: (id, payload) => request("POST", `/api/tasks/${id}/decide-auto`, payload),

  // The assistant bubble. `history` is the prior turns, oldest first; the
  // account snapshot is rebuilt server-side on every turn, never sent up.
  chat: (message, history) => request("POST", "/api/chat", { message, history }),

  // Privacy: the notice is public (it has to be readable before an account
  // exists); everything else is scoped to the signed-in user.
  privacyNotice: () => request("GET", "/api/privacy/notice"),
  getConsent: () => request("GET", "/api/privacy/consent"),
  updateConsent: (payload) => request("PATCH", "/api/privacy/consent", payload),
  exportData: () => request("GET", "/api/privacy/export"),
  eraseLearning: () => request("DELETE", "/api/privacy/learning-data"),
  deleteAccount: () => request("DELETE", "/api/privacy/account"),

  // The feedback loop.
  rateDecision: (taskId, payload) =>
    request("POST", `/api/feedback/decision/${taskId}`, payload),
  learningProfile: () => request("GET", "/api/feedback/profile"),
  pendingFeedback: () => request("GET", "/api/feedback/pending"),

  explain: () => request("POST", "/api/insights/explain"),
  history: () => request("GET", "/api/insights/history"),
  behavioral: () => request("GET", "/api/insights/behavioral"),
  // Decision tempo. `granularity` is "day" | "week", or null to let the
  // span of the history choose for itself.
  tempo: (granularity) =>
    request("GET", `/api/insights/tempo${granularity ? `?granularity=${granularity}` : ""}`),
};
