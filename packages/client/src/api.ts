import type { ProgressEvent, SessionDetail, Workspace } from "./types.js";

export class SelfAgentClient {
  constructor(public readonly baseUrl: string) {}

  private url(path: string): string {
    return `${this.baseUrl.replace(/\/$/, "")}${path}`;
  }

  async health(): Promise<boolean> {
    try {
      const r = await fetch(this.url("/api/health"));
      return r.ok;
    } catch {
      return false;
    }
  }

  async listWorkspaces(): Promise<Workspace[]> {
    const r = await fetch(this.url("/api/workspaces"));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async addWorkspace(path: string, title?: string): Promise<Workspace> {
    const r = await fetch(this.url("/api/workspaces"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, title }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async createSession(workspaceId: string, title?: string): Promise<SessionDetail> {
    const r = await fetch(this.url(`/api/workspaces/${workspaceId}/sessions`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async getSession(sessionId: string): Promise<SessionDetail> {
    const r = await fetch(this.url(`/api/sessions/${sessionId}`));
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async patchSession(
    sessionId: string,
    fields: Record<string, unknown>,
  ): Promise<SessionDetail> {
    const r = await fetch(this.url(`/api/sessions/${sessionId}`), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  async deleteSession(sessionId: string): Promise<void> {
    const r = await fetch(this.url(`/api/sessions/${sessionId}`), {
      method: "DELETE",
    });
    if (!r.ok) throw new Error(await r.text());
  }

  async cancelRun(sessionId: string): Promise<void> {
    const r = await fetch(this.url(`/api/sessions/${sessionId}/cancel`), {
      method: "POST",
    });
    if (!r.ok) throw new Error(await r.text());
  }

  async answerAsk(
    sessionId: string,
    body: { ask_id: string; raw?: string; answers?: unknown[] },
  ): Promise<void> {
    const r = await fetch(this.url(`/api/sessions/${sessionId}/answer`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
  }

  /** 消费 SSE；回合状态机入口（学 QueryEngine「消费事件流」）。 */
  async *streamChat(
    sessionId: string,
    message: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ProgressEvent> {
    yield* this._stream(`/api/sessions/${sessionId}/chat/stream`, { message }, signal);
  }

  async *streamConfirm(
    sessionId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ProgressEvent> {
    yield* this._stream(`/api/sessions/${sessionId}/confirm/stream`, {}, signal);
  }

  private async *_stream(
    path: string,
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): AsyncGenerator<ProgressEvent> {
    const r = await fetch(this.url(path), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
    if (!r.ok || !r.body) {
      throw new Error((await r.text()) || `stream failed ${r.status}`);
    }
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const parts = buf.split("\n");
      buf = parts.pop() || "";
      for (const line of parts) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const raw = trimmed.slice(5).trim();
        if (!raw || raw === "[DONE]") continue;
        try {
          yield JSON.parse(raw) as ProgressEvent;
        } catch {
          /* ignore */
        }
      }
    }
  }
}

export async function waitForHealth(
  client: SelfAgentClient,
  opts: { timeoutMs?: number; intervalMs?: number } = {},
): Promise<boolean> {
  const timeoutMs = opts.timeoutMs ?? 30000;
  const intervalMs = opts.intervalMs ?? 200;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await client.health()) return true;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  return false;
}
