import { SelfAgentClient, type ProgressEvent, type SessionDetail, type Workspace } from "@selfagent/client";
import { useCallback, useEffect, useMemo, useState } from "react";

const client = new SelfAgentClient(
  typeof window !== "undefined" && window.location.port === "5173"
    ? ""
    : `${window.location.protocol}//${window.location.host}`,
);

type Live = {
  status: string;
  draft: string;
  skills: { skill: string; status: string }[];
  cancelled: boolean;
};

export function App() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("就绪");
  const [input, setInput] = useState("");
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [wsPath, setWsPath] = useState("");
  const [wsTitle, setWsTitle] = useState("");

  const refresh = useCallback(async () => {
    const list = await client.listWorkspaces();
    setWorkspaces(list);
  }, []);

  const loadSession = useCallback(async (sid: string) => {
    const detail = await client.getSession(sid);
    setSessionId(sid);
    setSession(detail);
    setLive(null);
    setError(null);
    setStatus("就绪");
  }, []);

  useEffect(() => {
    void refresh().catch((e) => setError(String(e)));
  }, [refresh]);

  const applyEvent = useCallback((ev: ProgressEvent) => {
    const t = ev.type;
    if (t === "status") {
      const msg = String(ev.message || ev.phase || "处理中…");
      setStatus(msg);
      setLive((prev) => ({
        status: msg,
        draft: prev?.draft || "",
        skills: prev?.skills || [],
        cancelled: prev?.cancelled || false,
      }));
    } else if (t === "assistant_delta") {
      if (ev.phase === "thinking") return;
      const delta = String(ev.delta || "");
      setLive((prev) => ({
        status: prev?.status || "处理中…",
        draft: ((prev?.draft || "") + delta).slice(-1200),
        skills: prev?.skills || [],
        cancelled: false,
      }));
    } else if (t === "skill") {
      const name = String(ev.skill || "?");
      const st = String(ev.status || "start");
      setLive((prev) => {
        const skills = [...(prev?.skills || [])];
        if (st === "start") skills.push({ skill: name, status: "start" });
        else {
          for (let i = skills.length - 1; i >= 0; i--) {
            if (skills[i].skill === name && skills[i].status === "start") {
              skills[i] = {
                skill: name,
                status: ev.ok === false ? "err" : "ok",
              };
              break;
            }
          }
        }
        return {
          status: String(ev.message || prev?.status || "处理中…"),
          draft: prev?.draft || "",
          skills: skills.slice(-12),
          cancelled: false,
        };
      });
    } else if (t === "cancelled") {
      setLive((prev) => ({
        status: String(ev.message || "已取消"),
        draft: "",
        skills: prev?.skills || [],
        cancelled: true,
      }));
      setStatus("已取消");
    } else if (t === "done") {
      const sess = ev.session as SessionDetail | undefined;
      if (sess) {
        setSession(sess);
        setSessionId(String(sess.session_id || sessionId || ""));
      }
      setLive(null);
      setBusy(false);
      setStatus("就绪");
      void refresh();
    } else if (t === "error") {
      setError(String(ev.message || "未知错误"));
      setBusy(false);
      setLive(null);
      setStatus("出错");
    }
  }, [refresh, sessionId]);

  const send = async () => {
    if (!sessionId || busy) return;
    const text = input.trim();
    if (!text) return;
    setInput("");
    setBusy(true);
    setError(null);
    setLive({ status: "开始处理…", draft: "", skills: [], cancelled: false });
    try {
      for await (const ev of client.streamChat(sessionId, text)) {
        applyEvent(ev);
      }
    } catch (e) {
      setError(String(e));
      setBusy(false);
      setStatus("出错");
    }
  };

  const cancel = async () => {
    if (!sessionId) return;
    try {
      await client.cancelRun(sessionId);
      setStatus("正在停止…");
    } catch (e) {
      setError(String(e));
    }
  };

  const confirmPlan = async () => {
    if (!sessionId || busy) return;
    setBusy(true);
    setLive({ status: "执行计划…", draft: "", skills: [], cancelled: false });
    try {
      for await (const ev of client.streamConfirm(sessionId)) {
        applyEvent(ev);
      }
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  };

  const addWs = async () => {
    if (!wsPath.trim()) return;
    try {
      await client.addWorkspace(wsPath.trim(), wsTitle.trim() || undefined);
      setWsPath("");
      setWsTitle("");
      await refresh();
    } catch (e) {
      setError(String(e));
    }
  };

  const newSession = async (wid: string) => {
    try {
      const s = await client.createSession(wid);
      await refresh();
      await loadSession(s.session_id);
    } catch (e) {
      setError(String(e));
    }
  };

  const title = useMemo(
    () => session?.title || session?.preview || "未选择会话",
    [session],
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="panel-head">仓库</div>
        <div className="add-row">
          <input
            placeholder="工作目录路径"
            value={wsPath}
            onChange={(e) => setWsPath(e.target.value)}
          />
          <input
            placeholder="显示名称（可选）"
            value={wsTitle}
            onChange={(e) => setWsTitle(e.target.value)}
          />
          <button type="button" className="btn" onClick={() => void addWs()}>
            添加工作目录
          </button>
        </div>
        <div style={{ overflow: "auto", flex: 1 }}>
          {workspaces.map((ws) => (
            <div key={ws.id} className="ws-block">
              <div className="ws-title">{ws.title || ws.id}</div>
              <div className="ws-path" title={ws.path}>
                {ws.path}
              </div>
              <button
                type="button"
                className="btn"
                style={{ margin: "6px 0", width: "100%" }}
                onClick={() => void newSession(ws.id)}
              >
                + 新对话
              </button>
              {(ws.sessions || [])
                .filter((s) => !s.archived)
                .map((s) => (
                  <div
                    key={s.session_id}
                    className={`sess ${s.session_id === sessionId ? "active" : ""}`}
                    onClick={() => void loadSession(s.session_id)}
                    title={s.preview || s.title}
                  >
                    {s.starred ? "★ " : ""}
                    {s.title || s.preview || "新对话"}
                  </div>
                ))}
            </div>
          ))}
        </div>
      </aside>

      <main className="chat">
        <div className="panel-head">{title}</div>
        <div className="status">{status}</div>
        <div className="messages">
          {!session && <div className="empty">添加工作目录并选择会话开始</div>}
          {(session?.turns || []).map((t, i) => (
            <div key={i}>
              <div className="bubble user">{t.user}</div>
              {t.answer ? (
                <div className="bubble assistant">{t.answer}</div>
              ) : null}
            </div>
          ))}
          {live && (
            <div className="bubble live">
              <div>{live.cancelled ? "已取消" : "助手 · 进行中"}</div>
              <div>{live.status}</div>
              <div>
                {live.skills.map((s, i) => (
                  <span key={i} className="skill-chip">
                    {s.status === "ok" ? "✓" : s.status === "err" ? "✗" : "…"}{" "}
                    {s.skill}
                  </span>
                ))}
              </div>
              {live.draft && !live.cancelled ? (
                <pre style={{ whiteSpace: "pre-wrap", marginTop: 8 }}>
                  {live.draft}
                </pre>
              ) : null}
            </div>
          )}
          {session?.pending_plan && (
            <div className="plan-box">
              <b>待确认计划</b>
              <div style={{ margin: "8px 0", fontSize: 13 }}>
                {String(
                  (session.pending_plan as { summary?: string }).summary ||
                    (session.pending_plan as { text?: string }).text ||
                    "有待执行的计划",
                )}
              </div>
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={() => void confirmPlan()}
              >
                确认执行
              </button>
            </div>
          )}
          {error && (
            <div className="bubble" style={{ color: "var(--danger)" }}>
              {error}
            </div>
          )}
        </div>
        <div className="composer">
          <textarea
            value={input}
            placeholder={busy ? "运行中可点停止…" : "发送消息…"}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                void send();
              }
            }}
          />
          {busy ? (
            <button type="button" className="btn danger" onClick={() => void cancel()}>
              ■
            </button>
          ) : (
            <button
              type="button"
              className="btn primary"
              disabled={!sessionId}
              onClick={() => void send()}
            >
              ↑
            </button>
          )}
        </div>
      </main>

      <aside className="inspector">
        <div className="panel-head">过程 / 改动</div>
        <div className="empty" style={{ textAlign: "left" }}>
          {session?.detail_text ? (
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>
              {String(session.detail_text).slice(0, 8000)}
            </pre>
          ) : (
            "运行后在此查看细节。Electron 默认也可直接加载完整 web SPA。"
          )}
        </div>
      </aside>
    </div>
  );
}
