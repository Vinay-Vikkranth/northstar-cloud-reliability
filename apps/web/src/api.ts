export type Workspace = {
  id: string;
  name: string;
  slug: string;
  synthetic: boolean;
  roles: string[];
};

export type Session = {
  username: string;
  csrf: string;
  workspaces: Workspace[];
  workspaceId: string;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function csrfFromCookie(): string {
  const row = document.cookie.split("; ").find((item) => item.startsWith("cir_csrf="));
  return row ? decodeURIComponent(row.split("=").slice(1).join("=")) : "";
}

export async function api<T>(path: string, session: Session | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (session) {
    headers.set("X-CSRF-Token", session.csrf);
    headers.set("X-Workspace-Id", session.workspaceId);
  }
  const response = await fetch(path, { ...init, headers, credentials: "include" });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const message = body?.error?.message || body?.detail?.message || "The request failed";
    throw new ApiError(response.status, message);
  }
  return body as T;
}

export async function login(username: string, password: string): Promise<Session> {
  const body = await api<{ username: string; csrf_token: string; workspaces: Workspace[] }>("/api/v1/auth/login", null, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  const preferred = sessionStorage.getItem("cir_workspace");
  const workspace = body.workspaces.find((item) => item.id === preferred) || body.workspaces[0];
  if (!workspace) throw new ApiError(403, "This user has no workspace");
  return { username: body.username, csrf: body.csrf_token || csrfFromCookie(), workspaces: body.workspaces, workspaceId: workspace.id };
}

export async function restoreSession(): Promise<Session | null> {
  const csrf = csrfFromCookie();
  if (!csrf) return null;
  try {
    const body = await api<{ username: string; workspaces: Workspace[] }>("/api/v1/auth/me", {
      username: "",
      csrf,
      workspaces: [],
      workspaceId: "00000000-0000-0000-0000-000000000000",
    });
    const preferred = sessionStorage.getItem("cir_workspace");
    const workspace = body.workspaces.find((item) => item.id === preferred) || body.workspaces[0];
    if (!workspace) return null;
    return { username: body.username, csrf, workspaces: body.workspaces, workspaceId: workspace.id };
  } catch {
    return null;
  }
}

export function can(roles: string[], action: "viewer" | "operator" | "approver" | "administrator"): boolean {
  if (roles.includes("administrator")) return true;
  if (action === "viewer") return roles.some((role) => role === "viewer" || role === "operator" || role === "approver");
  return roles.includes(action);
}

export function ageLabel(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
