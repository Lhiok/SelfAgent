(() => {
  const state = {
    workspaces: [],
    workspaceId: null,
    sessionId: null,
    session: null,
    busy: false,
    showArchived: false,
    archiveOpen: {}, // workspaceId -> bool (local UI for archived group)
    live: null, // { userMessage, status, steps[], error, ask, draft, skills[], cancelled }
    streaming: false, // 是否有活动 SSE（刷新后为 false，但仍可能有 pending_ask）
    // 右侧面板：{ type:'change'|'plan'|'detail'|'ask', key, ... }
    inspector: null,
    todos: null, // { items, counts } 工作区任务清单
  };

  const $ = (id) => document.getElementById(id);
  const treeEl = $("workspace-tree");
  const messagesEl = $("messages");
  const statusText = $("status-text");
  const sessionTitle = $("session-title");
  const sessionPath = $("session-path");
  const inputMessage = $("input-message");
  const btnSend = $("btn-send");
  const btnCancelRun = $("btn-cancel-run");
  const btnDeleteSession = $("btn-delete-session");
  const btnToggleArchived = $("btn-toggle-archived");
  const selectDetail = $("select-detail");
  const formAddWs = $("form-add-ws");
  const inputWsAlias = $("input-ws-alias");
  const inputWsPath = $("input-ws-path");
  const changePanel = $("change-panel");
  const changePanelKind = $("change-panel-kind");
  const changePanelPath = $("change-panel-path");
  const changePanelBody = $("change-panel-body");
  const resizeInspector = $("resize-inspector");
  const inputInspectorWidth = $("input-inspector-width");
  const todoBar = $("todo-bar");
  const shellEl = document.getElementById("app");

  function emptyLive(overrides = {}) {
    return {
      userMessage: null,
      status: "处理中…",
      steps: [],
      error: null,
      ask: null,
      draft: "",
      skills: [],
      cancelled: false,
      ...overrides,
    };
  }

  const INSPECTOR_WIDTH_KEY = "selfagent.inspectorWidth";
  const SOUND_MUTE_KEY = "selfagent.soundMuted";
  const INSPECTOR_MIN = 240;
  const INSPECTOR_MAX = 1200;
  const CHAT_MIN = 280;

  let audioCtx = null;

  function unlockAudio() {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      if (!audioCtx) audioCtx = new Ctx();
      if (audioCtx.state === "suspended") {
        audioCtx.resume().catch(() => {});
      }
      return audioCtx;
    } catch {
      return null;
    }
  }

  function isSoundMuted() {
    return localStorage.getItem(SOUND_MUTE_KEY) === "1";
  }

  function setSoundMuted(muted) {
    localStorage.setItem(SOUND_MUTE_KEY, muted ? "1" : "0");
    syncSoundButton();
  }

  function syncSoundButton() {
    const btn = $("btn-sound");
    if (!btn) return;
    const muted = isSoundMuted();
    btn.textContent = muted ? "静" : "音";
    btn.classList.toggle("muted", muted);
    btn.title = muted ? "声音提示已关闭（点击开启）" : "声音提示已开启（点击关闭）";
  }

  /** 播放接近飞书消息的清脆双音提示（音量明显更大） */
  function playTone(ctx, { freq, start, dur, peak = 0.55, type = "sine", detune = 0 }) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    // 轻微谐波，让音色更“脆”、更像即时通讯提示
    const harm = ctx.createOscillator();
    const harmGain = ctx.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    osc.detune.value = detune;
    harm.type = "triangle";
    harm.frequency.value = freq * 2;
    harm.detune.value = detune;
    const t0 = ctx.currentTime + start;
    const attack = 0.012;
    const release = Math.max(0.05, dur - attack);
    gain.gain.setValueAtTime(0.0001, t0);
    gain.gain.exponentialRampToValueAtTime(peak, t0 + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + release);
    harmGain.gain.setValueAtTime(0.0001, t0);
    harmGain.gain.exponentialRampToValueAtTime(peak * 0.28, t0 + attack);
    harmGain.gain.exponentialRampToValueAtTime(0.0001, t0 + attack + release);
    osc.connect(gain);
    harm.connect(harmGain);
    gain.connect(ctx.destination);
    harmGain.connect(ctx.destination);
    osc.start(t0);
    harm.start(t0);
    osc.stop(t0 + dur + 0.04);
    harm.stop(t0 + dur + 0.04);
  }

  /** @param {"attention"|"complete"|"error"} kind */
  function playNotify(kind) {
    if (isSoundMuted()) return;
    const ctx = unlockAudio();
    if (!ctx) return;

    // attention ≈ 飞书消息：两声清脆「叮咚」；complete 三声上升；error 低沉两声
    if (kind === "attention") {
      playTone(ctx, { freq: 1046.5, start: 0, dur: 0.14, peak: 0.62, type: "sine" });
      playTone(ctx, { freq: 1568, start: 0.12, dur: 0.22, peak: 0.7, type: "sine" });
      return;
    }
    if (kind === "error") {
      playTone(ctx, { freq: 220, start: 0, dur: 0.18, peak: 0.55, type: "square" });
      playTone(ctx, { freq: 165, start: 0.16, dur: 0.28, peak: 0.5, type: "triangle" });
      return;
    }
    // complete
    playTone(ctx, { freq: 659.25, start: 0, dur: 0.1, peak: 0.48, type: "sine" });
    playTone(ctx, { freq: 830.61, start: 0.09, dur: 0.1, peak: 0.52, type: "sine" });
    playTone(ctx, { freq: 1046.5, start: 0.18, dur: 0.22, peak: 0.6, type: "sine" });
  }

  function notifyRunFinished(session, { error = false } = {}) {
    if (error) {
      playNotify("error");
      return;
    }
    // 计划待确认 = 需要用户授权
    if (session?.plan?.phase === "awaiting_confirm" || session?.pending_plan?.ok) {
      playNotify("attention");
      return;
    }
    playNotify("complete");
  }

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const text = await res.text();
    let data = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail || res.statusText;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function streamEvents(path, body, onEvent) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body ?? {}),
    });
    if (!res.ok) {
      const text = await res.text();
      let detail = res.statusText;
      try {
        const data = text ? JSON.parse(text) : null;
        detail = data?.detail || detail;
      } catch {
        if (text) detail = text;
      }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    if (!res.body) throw new Error("浏览器不支持流式响应");

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let donePayload = null;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const lines = part.split("\n");
        const dataLines = [];
        for (const line of lines) {
          if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        if (!dataLines.length) continue;
        let event;
        try {
          event = JSON.parse(dataLines.join("\n"));
        } catch {
          continue;
        }
        onEvent(event);
        if (event.type === "done") donePayload = event.session;
        if (event.type === "error") {
          throw new Error(event.message || "运行失败");
        }
      }
    }
    if (!donePayload) throw new Error("流式响应未完成");
    return donePayload;
  }

  function setBusy(busy, label) {
    state.busy = busy;
    statusText.textContent = label || (busy ? "处理中…" : "就绪");
    statusText.classList.toggle("busy", busy);
    const ready = Boolean(state.sessionId) && !busy;
    inputMessage.disabled = !ready;
    btnSend.disabled = !ready;
    btnDeleteSession.disabled = !ready;
    selectDetail.disabled = !ready;
    document.querySelectorAll(".seg-btn").forEach((b) => {
      b.disabled = !ready;
    });
    document.querySelectorAll(".btn-confirm-plan").forEach((b) => {
      b.disabled = busy;
    });
    btnToggleArchived.classList.toggle("active", state.showArchived);
    if (btnCancelRun) {
      btnCancelRun.classList.toggle("hidden", !(busy && state.streaming));
      btnCancelRun.disabled = !(busy && state.streaming);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function folderName(path) {
    const s = String(path || "").replace(/[\\/]+$/, "");
    const parts = s.split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || s || "工作区";
  }

  function workspaceAlias(ws) {
    const title = (ws?.title || "").trim();
    return title || folderName(ws?.path);
  }

  /** 长路径保留尾部，便于区分同名目录。 */
  function shortenPath(path, max = 34) {
    const s = String(path || "");
    if (s.length <= max) return s;
    return "…" + s.slice(-(max - 1));
  }

  function sessionsOf(ws) {
    return Array.isArray(ws.sessions) ? ws.sessions : [];
  }

  function findSessionMeta(sessionId) {
    for (const ws of state.workspaces) {
      const hit = sessionsOf(ws).find((s) => s.session_id === sessionId);
      if (hit) return { workspace: ws, session: hit };
    }
    return null;
  }

  function renderTree() {
    treeEl.innerHTML = "";
    btnToggleArchived.classList.toggle("active", state.showArchived);

    if (!state.workspaces.length) {
      treeEl.innerHTML = `<div class="tree-empty">尚未添加工作目录</div>`;
      return;
    }

    for (const ws of state.workspaces) {
      const collapsed = Boolean(ws.collapsed);
      const all = sessionsOf(ws);
      const active = all.filter((s) => !s.archived);
      const archived = all.filter((s) => s.archived);
      const showArchiveGroup = state.showArchived && archived.length > 0;
      const archiveExpanded = Boolean(state.archiveOpen[ws.id]);

      const node = document.createElement("div");
      node.className = `ws-node${ws.id === state.workspaceId ? " current" : ""}${
        collapsed ? " collapsed" : ""
      }`;

      const alias = workspaceAlias(ws);
      const pathShort = shortenPath(ws.path);
      const head = document.createElement("div");
      head.className = "ws-head";
      head.innerHTML = `
        <button type="button" class="chevron" aria-label="折叠">${collapsed ? "▶" : "▼"}</button>
        <div class="ws-info" title="${escapeHtml(ws.path)}">
          <div class="title">${escapeHtml(alias)}</div>
          <div class="meta">${escapeHtml(pathShort)} · ${active.length} 对话${
            archived.length ? ` · ${archived.length} 归档` : ""
          }</div>
        </div>
        <div class="ws-actions">
          <button type="button" class="icon-btn rename" title="设置别名">✎</button>
          <button type="button" class="btn ghost sm btn-new" title="新建对话">+</button>
        </div>
      `;

      head.querySelector(".chevron").onclick = (e) => {
        e.stopPropagation();
        toggleWorkspaceCollapsed(ws.id, !collapsed);
      };
      head.querySelector(".ws-info").onclick = () => {
        state.workspaceId = ws.id;
        if (collapsed) toggleWorkspaceCollapsed(ws.id, false);
        else renderTree();
      };
      head.querySelector(".title").ondblclick = (e) => {
        e.stopPropagation();
        startRenameWorkspace(ws, head);
      };
      head.querySelector(".rename").onclick = (e) => {
        e.stopPropagation();
        startRenameWorkspace(ws, head);
      };
      head.querySelector(".btn-new").onclick = (e) => {
        e.stopPropagation();
        createSession(ws.id);
      };

      node.appendChild(head);

      if (!collapsed) {
        const list = document.createElement("ul");
        list.className = "session-list";

        if (!active.length && !showArchiveGroup) {
          const empty = document.createElement("li");
          empty.className = "session-empty";
          empty.textContent = "暂无对话，点击 + 新建";
          list.appendChild(empty);
        }

        for (const s of active) {
          list.appendChild(sessionRow(ws, s));
        }

        if (showArchiveGroup) {
          const archHead = document.createElement("li");
          archHead.className = "archive-head";
          archHead.innerHTML = `<button type="button" class="chevron sm">${
            archiveExpanded ? "▼" : "▶"
          }</button><span>已归档 (${archived.length})</span>`;
          archHead.onclick = () => {
            state.archiveOpen[ws.id] = !archiveExpanded;
            renderTree();
          };
          list.appendChild(archHead);
          if (archiveExpanded) {
            for (const s of archived) {
              list.appendChild(sessionRow(ws, s, true));
            }
          }
        }

        node.appendChild(list);
      }

      treeEl.appendChild(node);
    }
  }

  function sessionRow(ws, s, isArchived = false) {
    const li = document.createElement("li");
    li.className = `session-item${s.session_id === state.sessionId ? " active" : ""}${
      isArchived ? " archived" : ""
    }${s.starred ? " starred" : ""}`;
    const title = s.title || s.preview || "新对话";
    li.innerHTML = `
      <button type="button" class="icon-btn star" title="${s.starred ? "取消收藏" : "收藏"}">${
        s.starred ? "★" : "☆"
      }</button>
      <div class="session-main">
        <div class="title">${escapeHtml(title)}</div>
        <div class="meta">${s.turn_count || 0} 轮 · ${escapeHtml((s.updated_at || "").slice(0, 19))}</div>
      </div>
      <button type="button" class="icon-btn archive" title="${
        s.archived ? "取消归档" : "归档"
      }">${s.archived ? "↩" : "▤"}</button>
    `;
    li.querySelector(".session-main").onclick = () => selectSession(s.session_id, ws.id);
    li.querySelector(".star").onclick = (e) => {
      e.stopPropagation();
      patchSessionFlags(s.session_id, { starred: !s.starred });
    };
    li.querySelector(".archive").onclick = (e) => {
      e.stopPropagation();
      patchSessionFlags(s.session_id, { archived: !s.archived });
    };
    return li;
  }

  function setTodos(todos) {
    if (!todos || !Array.isArray(todos.items)) {
      state.todos = null;
    } else {
      state.todos = {
        items: todos.items,
        counts: todos.counts || {},
        updated_at: todos.updated_at || null,
      };
    }
    renderTodoBar();
  }

  function renderTodoBar() {
    if (!todoBar) return;
    const todos = state.todos;
    const items = (todos && todos.items) || [];
    if (!items.length) {
      todoBar.classList.add("hidden");
      todoBar.innerHTML = "";
      return;
    }
    const c = todos.counts || {};
    const done = c.done || 0;
    const total = c.total || items.length;
    const pct = total ? Math.round((done / total) * 100) : 0;
    const active =
      items.find((it) => it.status === "in_progress") ||
      items.find((it) => it.status === "pending");
    todoBar.classList.remove("hidden");
    todoBar.innerHTML = `
      <div class="todo-bar-head">
        <span class="todo-bar-title">任务清单</span>
        <span class="todo-bar-meta">${done}/${total} · ${pct}%</span>
      </div>
      <div class="todo-bar-track"><div class="todo-bar-fill" style="width:${pct}%"></div></div>
      <ul class="todo-bar-list">
        ${items
          .slice(0, 8)
          .map((it) => {
            const st = it.status || "pending";
            const mark =
              st === "done" ? "✓" : st === "in_progress" ? "→" : st === "cancelled" ? "×" : "·";
            return `<li class="todo-item st-${st}"><span class="todo-mark">${mark}</span><span class="todo-text">${escapeHtml(
              it.title || ""
            )}</span></li>`;
          })
          .join("")}
        ${items.length > 8 ? `<li class="todo-more">…另有 ${items.length - 8} 项</li>` : ""}
      </ul>
      ${
        active
          ? `<div class="todo-bar-current">当前：${escapeHtml(active.title || "")}</div>`
          : ""
      }
    `;
  }

  function renderSession() {
    const s = state.session;
    const live = state.live;

    if (!s && !live) {
      sessionTitle.textContent = "选择或新建对话";
      sessionPath.textContent = "";
      messagesEl.innerHTML = `<div class="empty-state"><p class="empty-title">SelfAgent</p><p>添加工作目录，再新建对话开始任务。</p></div>`;
      setTodos(null);
      return;
    }

    if (s) {
      sessionTitle.textContent = s.title || s.preview || "对话";
      sessionPath.textContent = s.workdir || "";
      selectDetail.value = s.detail || "off";
      document.querySelectorAll(".seg-btn").forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.mode === (s.mode || "agent"));
      });
      if (s.todos) setTodos(s.todos);
      else renderTodoBar();
    }

    messagesEl.innerHTML = "";
    const turns = (s && s.turns) || [];
    if (!turns.length && !live) {
      messagesEl.innerHTML = `<div class="empty-state"><p class="empty-title">新对话</p><p>在下方输入任务，Agent 将在此工作目录中执行。</p></div>`;
      return;
    }

    const awaitingPlan =
      (s && s.plan && s.plan.phase === "awaiting_confirm") ||
      (s && s.pending_plan && s.pending_plan.ok);
    const pendingPlan = awaitingPlan && s.pending_plan && s.pending_plan.ok ? s.pending_plan : null;
    const lastTurnIndex = turns.length ? turns[turns.length - 1].index : -1;

    for (const t of turns) {
      messagesEl.appendChild(bubble("user", t.user));
      if (t.ask_answers && t.ask_answers.length) {
        messagesEl.appendChild(renderAskAnswersCard(t.ask_answers));
      }
      const planForTurn =
        pendingPlan && !live && t.index === lastTurnIndex ? pendingPlan : null;
      const asst = renderAssistantBubble(t.answer || "(无回复)", {
        plan: planForTurn,
      });
      if (t.changes && t.changes.length) {
        asst.appendChild(renderChangeList(t.changes, `turn-${t.index}`));
      }
      if (planForTurn) {
        asst.appendChild(renderPlanActions(planForTurn));
      }
      if (t._detail_text) {
        asst.appendChild(
          renderDetailLink(t._detail_text, `turn-${t.index}-detail`, "运行细节")
        );
      }
      messagesEl.appendChild(asst);
    }

    if (live) {
      if (live.userMessage) {
        messagesEl.appendChild(bubble("user", live.userMessage));
      }
      if (live.submittedAnswers && live.submittedAnswers.length) {
        messagesEl.appendChild(renderAskAnswersCard(live.submittedAnswers));
      }
      messagesEl.appendChild(renderLivePanel(live));
    }

    messagesEl.scrollTop = messagesEl.scrollHeight;
    updateAskFallbackButton();
  }

  function renderPlanActions(plan) {
    // 计划摘要已在助手气泡正文中展示，这里只保留操作，避免重复
    const wrap = document.createElement("div");
    wrap.className = "plan-actions";

    const row = document.createElement("div");
    row.className = "plan-action-row";

    const detailBtn = document.createElement("button");
    detailBtn.type = "button";
    detailBtn.className = `btn ghost sm${
      state.inspector?.type === "plan" ? " active" : ""
    }`;
    detailBtn.textContent = "查看细节";
    detailBtn.onclick = () => openPlanPanel(plan);

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "btn primary sm btn-confirm-plan";
    confirmBtn.textContent = "确认执行";
    confirmBtn.disabled = state.busy;
    confirmBtn.onclick = () => confirmPlan();

    row.appendChild(detailBtn);
    row.appendChild(confirmBtn);
    wrap.appendChild(row);
    return wrap;
  }

  function renderDetailLink(text, key, title) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `btn ghost sm detail-link${
      state.inspector?.type === "detail" && state.inspector?.key === key ? " active" : ""
    }`;
    btn.textContent = title || "运行细节";
    btn.onclick = () => openDetailPanel(text, key, title);
    return btn;
  }

  function renderLiveSkills(skills) {
    const row = document.createElement("div");
    row.className = "live-skills";
    for (const s of skills || []) {
      const chip = document.createElement("span");
      const st = s.status || "running";
      chip.className = `live-skill-chip ${st === "start" || st === "running" ? "running" : st === "ok" ? "ok" : st === "err" ? "err" : ""}`;
      const mark =
        st === "ok" ? "✓" : st === "err" ? "✗" : st === "end" ? "·" : "…";
      chip.textContent = `${mark} ${s.skill || "?"}`;
      row.appendChild(chip);
    }
    return row;
  }

  function renderLivePanel(live) {
    const wrap = document.createElement("div");
    wrap.className = `bubble assistant live-run${live.cancelled ? " cancelled" : ""}`;

    const role = document.createElement("div");
    role.className = "role";
    role.textContent = live.cancelled
      ? "助手 · 已取消"
      : live.ask
        ? "助手 · 等待确认"
        : "助手 · 进行中";
    wrap.appendChild(role);

    const status = document.createElement("div");
    status.className = "live-status";
    status.innerHTML = `<span class="spinner"></span><span class="live-status-text"></span>`;
    status.querySelector(".live-status-text").textContent = live.status || "处理中…";
    wrap.appendChild(status);

    if ((live.skills || []).length) {
      wrap.appendChild(renderLiveSkills(live.skills));
    }

    if (live.draft && !live.ask) {
      const draft = document.createElement("div");
      draft.className = "live-draft";
      draft.textContent = live.draft;
      wrap.appendChild(draft);
    }

    // 等待 ask_user 时：展示方案正文 + 打开面板按钮；过程默认折叠
    if (live.ask) {
      const proposal = pickLiveProposal(live);
      if (proposal) {
        const body = document.createElement("div");
        body.className = "body live-proposal";
        setMarkdown(body, proposal);
        wrap.appendChild(body);
      }
      wrap.appendChild(renderAskLiveNotice(live.ask));
      const steps = live.steps || [];
      if (steps.length) {
        const details = document.createElement("details");
        details.className = "live-process-details";
        const summary = document.createElement("summary");
        summary.textContent = `过程（${steps.length} 步）`;
        details.appendChild(summary);
        const timeline = document.createElement("div");
        timeline.className = "live-timeline";
        for (const step of steps) {
          timeline.appendChild(renderLiveStep(step, { compactAsk: true }));
        }
        details.appendChild(timeline);
        wrap.appendChild(details);
      }
    } else {
      const timeline = document.createElement("div");
      timeline.className = "live-timeline";
      for (const step of live.steps || []) {
        timeline.appendChild(renderLiveStep(step));
      }
      wrap.appendChild(timeline);
    }

    const allChanges = [];
    for (const step of live.steps || []) {
      if (step.changes?.length) allChanges.push(...step.changes);
    }
    if (allChanges.length) {
      wrap.appendChild(renderChangeList(allChanges, "live-all"));
    }

    if (live.error) {
      const err = document.createElement("div");
      err.className = "live-error";
      err.textContent = live.error;
      wrap.appendChild(err);
    }
    return wrap;
  }

  function findOptionDetailInText(text, letter) {
    const L = String(letter || "").trim();
    if (!L || !text) return "";
    const re = new RegExp(
      `(?:^|\\n)\\s*(?:[-*]\\s*)?(?:\\*\\*)?${L}(?:\\*\\*)?\\s*[.、:：)\\]]\\s*(.+?)(?=\\n\\s*(?:[-*]\\s*)?(?:\\*\\*)?[A-Za-z](?:\\*\\*)?\\s*[.、:：)\\]]|\\n\\n|$)`,
      "is"
    );
    const m = String(text).match(re);
    return m ? m[1].replace(/\s+/g, " ").trim() : "";
  }

  function optionLooksShort(opt) {
    const s = String(opt || "").trim();
    if (!s) return true;
    // 仅字母/序号，或「A」「A.」「方案A」这类短标签
    if (/^[A-Za-z]$/.test(s)) return true;
    if (/^[A-Za-z]\s*[.、:：]?$/.test(s)) return true;
    if (/^方案\s*[A-Za-z]$/i.test(s)) return true;
    if (s.length <= 2) return true;
    return false;
  }

  function enrichAskOptions(question, options, context) {
    const rawOpts = (Array.isArray(options) ? options : []).map(String);
    if (!rawOpts.length) return rawOpts;
    const blob = [question, context].filter(Boolean).join("\n");
    const shortCount = rawOpts.filter(optionLooksShort).length;
    // 多数选项是短标签时，尝试从题干/背景里抽取完整说明
    if (shortCount < Math.ceil(rawOpts.length / 2)) return rawOpts;

    return rawOpts.map((opt, i) => {
      if (!optionLooksShort(opt) && String(opt).trim().length > 8) return String(opt).trim();
      const letterMatch = String(opt).trim().match(/^([A-Za-z])/);
      const letter = letterMatch ? letterMatch[1].toUpperCase() : String.fromCharCode(65 + i);
      const detail = findOptionDetailInText(blob, letter);
      if (!detail) return String(opt).trim();
      // 避免 detail 已自带字母前缀时重复
      const cleaned = detail.replace(new RegExp(`^${letter}\\s*[.、:：)\\]]\\s*`, "i"), "");
      return `${letter}. ${cleaned}`;
    });
  }

  function stripEmbeddedOptionsFromQuestion(question, options) {
    let q = String(question || "");
    if (!q) return q;
    const letters = (options || [])
      .map((opt, i) => {
        const m = String(opt).trim().match(/^([A-Za-z])/);
        return m ? m[1].toUpperCase() : String.fromCharCode(65 + i);
      })
      .filter(Boolean);
    for (const letter of [...new Set(letters)]) {
      q = q.replace(
        new RegExp(
          `(?:^|\\n)\\s*(?:[-*]\\s*)?(?:\\*\\*)?${letter}(?:\\*\\*)?\\s*[.、:：)\\]][^\\n]*`,
          "gi"
        ),
        ""
      );
    }
    return q.replace(/\n{3,}/g, "\n\n").trim();
  }

  function matchAskDefault(def, options) {
    const d = String(def ?? "").trim();
    if (!d) return -1;
    const byIndex = options.findIndex((opt, idx) => String(idx + 1) === d || opt === d);
    if (byIndex >= 0) return byIndex;
    // default=A 且选项为「A. xxx」
    return options.findIndex((opt) => {
      const s = String(opt).trim();
      return (
        s === d ||
        s.toUpperCase().startsWith(`${d.toUpperCase()}.`) ||
        s.toUpperCase().startsWith(`${d.toUpperCase()}：`) ||
        s.toUpperCase().startsWith(`${d.toUpperCase()}:`) ||
        s.toUpperCase().startsWith(`${d.toUpperCase()}、`)
      );
    });
  }

  function normalizeAskQuestions(eventOrAsk) {
    const src = eventOrAsk || {};
    let list = Array.isArray(src.questions) ? src.questions : null;
    if (!list || !list.length) {
      list = [
        {
          id: "1",
          question: src.question || "请选择",
          options: src.options || [],
          allow_multiple: Boolean(src.allow_multiple),
          allow_custom: src.allow_custom !== false,
          default: src.default,
          context: src.context || "",
        },
      ];
    }
    return list.map((q, i) => {
      const id = String(q.id || i + 1);
      const rawQuestion = String(q.question || "").trim() || `问题 ${i + 1}`;
      const context = String(q.context || "");
      const options = enrichAskOptions(
        rawQuestion,
        Array.isArray(q.options) ? q.options : [],
        context
      );
      const question = stripEmbeddedOptionsFromQuestion(rawQuestion, options) || rawQuestion;
      let choice = q._choice || "";
      if (!choice && q.default != null && !q.allow_multiple) {
        const byIndex = matchAskDefault(q.default, options);
        if (byIndex >= 0) choice = String(byIndex + 1);
      }
      return {
        id,
        question,
        options,
        allow_multiple: Boolean(q.allow_multiple),
        allow_custom: q.allow_custom !== false,
        default: q.default,
        context,
        _choice: choice,
        _custom: q._custom || "",
        _selected: Array.isArray(q._selected) ? q._selected : [],
      };
    });
  }

  function openAskInspector(ask) {
    if (!ask?.ask_id) return;
    state.inspector = {
      type: "ask",
      key: `ask-${ask.ask_id}`,
      ask,
    };
    renderInspector();
    updateAskFallbackButton();
  }

  function applyPermissionAskEvent(event) {
    if (!event?.ask_id) return;
    if (!state.live) state.live = emptyLive({ status: "等待授权…" });
    state.live.permission = {
      ask_id: event.ask_id,
      skill: event.skill || "",
      arguments: event.arguments || {},
      reason: event.reason || "",
      matched_rule: event.matched_rule || "",
      suggestions: event.suggestions || [],
      submitting: false,
    };
    state.live.status = `等待授权: ${event.skill || "?"}`;
    playNotify("attention");
    state.inspector = {
      type: "permission",
      key: `perm-${event.ask_id}`,
      permission: state.live.permission,
    };
    renderInspector();
  }

  function renderPermissionInspector(perm) {
    changePanelKind.textContent = "授权";
    changePanelKind.className = "change-panel-kind kind-ask";
    setPanelPathNode(perm.skill || "权限确认");
    const wrap = document.createElement("div");
    wrap.className = "ask-panel";
    const q = document.createElement("div");
    q.className = "ask-question";
    q.textContent = perm.reason || `允许执行 ${perm.skill}？`;
    wrap.appendChild(q);
    const meta = document.createElement("pre");
    meta.className = "code-block";
    meta.textContent = JSON.stringify(
      {
        skill: perm.skill,
        arguments: perm.arguments,
        matched_rule: perm.matched_rule,
        suggestions: perm.suggestions,
      },
      null,
      2
    );
    wrap.appendChild(meta);
    const actions = document.createElement("div");
    actions.className = "ask-actions";
    const allowBtn = document.createElement("button");
    allowBtn.className = "primary";
    allowBtn.textContent = perm.submitting ? "提交中…" : "允许";
    allowBtn.disabled = Boolean(perm.submitting);
    allowBtn.onclick = () => submitPermissionAnswer(perm, true);
    const denyBtn = document.createElement("button");
    denyBtn.className = "ghost";
    denyBtn.textContent = "拒绝";
    denyBtn.disabled = Boolean(perm.submitting);
    denyBtn.onclick = () => submitPermissionAnswer(perm, false);
    actions.appendChild(allowBtn);
    actions.appendChild(denyBtn);
    wrap.appendChild(actions);
    changePanelBody.appendChild(wrap);
  }

  async function submitPermissionAnswer(perm, allow) {
    if (!state.sessionId || !perm?.ask_id || perm.submitting) return;
    perm.submitting = true;
    renderInspector();
    try {
      await api(`/api/sessions/${state.sessionId}/permission`, {
        method: "POST",
        body: JSON.stringify({ ask_id: perm.ask_id, allow: Boolean(allow) }),
      });
      if (state.live) {
        state.live.permission = null;
        state.live.status = allow ? "已授权，继续…" : "已拒绝授权";
      }
      if (state.inspector?.type === "permission") {
        state.inspector = null;
        renderInspector();
      }
      renderSession();
    } catch (err) {
      perm.submitting = false;
      alert(err.message || String(err));
      renderInspector();
    }
  }

  function currentPendingAsk() {
    if (state.live?.ask?.ask_id) return state.live.ask;
    const event = state.session?.pending_ask;
    if (!event?.ask_id) return null;
    return {
      ask_id: event.ask_id,
      question: event.question || "",
      options: event.options || [],
      questions: normalizeAskQuestions(event),
      submitting: false,
    };
  }

  function openPendingAskPanel() {
    let ask = currentPendingAsk();
    if (!ask && state.session?.pending_ask?.ask_id) {
      syncPendingAskFromSession(state.session);
      ask = currentPendingAsk();
    }
    if (!ask) {
      alert("当前没有待确认的问题。");
      updateAskFallbackButton();
      return;
    }
    if (!state.live?.ask || state.live.ask.ask_id !== ask.ask_id) {
      if (!state.live) {
        state.live = emptyLive({ status: "等待你的选择…" });
      }
      state.live.ask = ask;
      state.live.status =
        (ask.questions || []).length > 1
          ? `等待你确认 ${ask.questions.length} 个问题…`
          : "等待你的选择…";
    }
    openAskInspector(ask);
    renderSession();
    // 滚到面板顶部，避免窄屏时面板在视口外
    try {
      changePanel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      /* ignore */
    }
  }

  function updateAskFallbackButton() {
    const btn = $("btn-open-ask");
    if (!btn) return;
    const ask = currentPendingAsk();
    const pending = Boolean(ask?.ask_id || state.session?.pending_ask?.ask_id);
    btn.classList.toggle("hidden", !pending);
    if (!pending) return;
    const open = state.inspector?.type === "ask" && !changePanel.classList.contains("hidden");
    const n = (ask?.questions || []).length || 1;
    btn.textContent = open
      ? "确认面板已打开"
      : n > 1
        ? `打开确认面板（${n}）`
        : "打开确认面板";
    btn.disabled = false;
    btn.classList.toggle("ghost", open);
    btn.classList.toggle("primary", !open);
  }

  function renderAskLiveNotice(ask) {
    const wrap = document.createElement("div");
    wrap.className = "ask-panel ask-live-notice";
    const n = (ask.questions || []).length || 1;
    const title = document.createElement("div");
    title.className = "ask-question";
    title.textContent =
      n > 1
        ? `有 ${n} 个问题待确认。若右侧未弹出，请点下方按钮打开。`
        : "有问题待确认。若右侧未弹出，请点下方按钮打开。";
    wrap.appendChild(title);
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary sm btn-open-ask-inline";
    const open =
      state.inspector?.type === "ask" &&
      state.inspector?.ask?.ask_id === ask.ask_id &&
      !changePanel.classList.contains("hidden");
    btn.textContent = open ? "重新打开确认面板" : "打开确认面板";
    btn.onclick = () => openPendingAskPanel();
    wrap.appendChild(btn);
    return wrap;
  }

  function renderAskInspector(ask) {
    changePanelKind.textContent = "确认";
    changePanelKind.className = "change-panel-kind kind-ask";
    const n = (ask.questions || []).length || 1;
    setPanelPathNode(n > 1 ? `${n} 个问题` : "待确认问题");

    const wrap = document.createElement("div");
    wrap.className = "ask-inspector";

    const hint = document.createElement("div");
    hint.className = "ask-inspector-hint";
    hint.textContent =
      n > 1
        ? "请逐题选择（可随时改选），全部选好后点底部确认。"
        : "请选择后确认；确认前可改选。";
    wrap.appendChild(hint);

    (ask.questions || []).forEach((q, qi) => {
      wrap.appendChild(renderAskQuestionBlock(ask, q, qi));
    });

    const footer = document.createElement("div");
    footer.className = "ask-inspector-footer";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary";
    btn.textContent = ask.submitting
      ? "提交中…"
      : n > 1
        ? "确认全部选择"
        : "确认选择";
    btn.disabled = Boolean(ask.submitting);
    btn.onclick = () => submitAskAnswers(ask);
    footer.appendChild(btn);
    wrap.appendChild(footer);

    changePanelBody.appendChild(wrap);
  }

  function renderAskQuestionBlock(ask, q, qi) {
    const block = document.createElement("div");
    block.className = "ask-q-block";

    const head = document.createElement("div");
    head.className = "ask-q-head";
    head.textContent = `问题 ${qi + 1}`;
    block.appendChild(head);

    const question = document.createElement("div");
    question.className = "ask-question md-body";
    setMarkdown(question, q.question);
    block.appendChild(question);

    if (q.context) {
      const ctx = document.createElement("div");
      ctx.className = "ask-context";
      ctx.textContent = q.context;
      block.appendChild(ctx);
    }

    const submitting = Boolean(ask.submitting);
    const options = q.options || [];
    const defaultIdx = q.default != null ? matchAskDefault(q.default, options) : -1;

    if (q.allow_multiple) {
      const list = document.createElement("div");
      list.className = "ask-options multi";
      const selected = new Set(q._selected || []);
      options.forEach((opt, i) => {
        const id = String(i + 1);
        const label = document.createElement("label");
        label.className = "ask-option-check";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = id;
        cb.checked = selected.has(id);
        cb.disabled = submitting;
        cb.onchange = () => {
          const next = new Set(q._selected || []);
          if (cb.checked) next.add(id);
          else next.delete(id);
          q._selected = [...next];
        };
        label.appendChild(cb);
        const text = document.createElement("span");
        text.textContent = `${id}) ${opt}${defaultIdx === i ? "（默认）" : ""}`;
        label.appendChild(text);
        list.appendChild(label);
      });
      block.appendChild(list);
    } else {
      const list = document.createElement("div");
      list.className = "ask-options";
      options.forEach((opt, i) => {
        const id = String(i + 1);
        const btn = document.createElement("button");
        btn.type = "button";
        const selected = q._choice === id && !(q._custom || "").trim();
        btn.className = `btn ghost sm ask-option-btn${selected ? " selected" : ""}`;
        btn.textContent = `${id}) ${opt}${defaultIdx === i ? "（默认）" : ""}`;
        btn.disabled = submitting;
        btn.onclick = () => {
          q._choice = id;
          q._custom = "";
          if (state.inspector?.type === "ask") renderInspector();
        };
        list.appendChild(btn);
      });
      block.appendChild(list);
    }

    if (q.allow_custom) {
      const input = document.createElement("input");
      input.type = "text";
      input.className = "ask-custom";
      input.placeholder = "或输入自定义方案";
      input.disabled = submitting;
      input.value = q._custom || "";
      input.oninput = () => {
        q._custom = input.value;
        if (input.value.trim()) {
          q._choice = "";
          q._selected = [];
        }
      };
      block.appendChild(input);
    }

    return block;
  }

  function formatAskAnswersForDisplay(ask, answers) {
    const byId = new Map((answers || []).map((a) => [String(a.id), a]));
    return (ask.questions || []).map((q, i) => {
      const hit = byId.get(String(q.id)) || answers[i] || {};
      const raw = String(hit.raw || "").trim();
      let selected = [];
      if (q.allow_multiple) {
        const parts = raw.split(",").map((x) => x.trim()).filter(Boolean);
        selected = parts.map((p) => {
          if (/^\d+$/.test(p)) {
            const idx = Number(p) - 1;
            return q.options[idx] || p;
          }
          return p;
        });
      } else if (/^\d+$/.test(raw)) {
        const idx = Number(raw) - 1;
        selected = [q.options[idx] || raw];
      } else if (raw) {
        selected = [raw];
      }
      return {
        id: String(q.id || i + 1),
        question: q.question || `问题 ${i + 1}`,
        selected,
        raw,
      };
    });
  }

  function renderAskAnswersCard(items) {
    const wrap = document.createElement("div");
    wrap.className = "bubble ask-answers";
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = "你的选择";
    wrap.appendChild(role);
    const list = document.createElement("div");
    list.className = "ask-answers-list";
    (items || []).forEach((item, i) => {
      const row = document.createElement("div");
      row.className = "ask-answer-row";
      const q = document.createElement("div");
      q.className = "ask-answer-q";
      q.textContent = item.question || `问题 ${i + 1}`;
      row.appendChild(q);
      const a = document.createElement("div");
      a.className = "ask-answer-a";
      const selected = Array.isArray(item.selected)
        ? item.selected
        : item.selected != null
          ? [String(item.selected)]
          : [];
      a.textContent = selected.length ? selected.join("、") : String(item.raw || "（未记录）");
      row.appendChild(a);
      list.appendChild(row);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function collectAskAnswers(ask) {
    const answers = [];
    for (const q of ask.questions || []) {
      const custom = (q._custom || "").trim();
      let raw = custom;
      if (!raw) {
        if (q.allow_multiple) {
          raw = (q._selected || []).slice().sort().join(",");
        } else {
          raw = q._choice || "";
        }
      }
      answers.push({ id: q.id, raw });
    }
    return answers;
  }

  async function submitAskAnswers(ask) {
    if (!state.sessionId || !ask || ask.submitting) return;
    if (state.live?.ask?.ask_id !== ask.ask_id) return;
    const answers = collectAskAnswers(ask);
    const missing = answers.findIndex((a) => !String(a.raw || "").trim());
    if (missing >= 0) {
      alert(`请先完成问题 ${missing + 1} 的选择`);
      return;
    }
    ask.submitting = true;
    if (state.live?.ask) {
      state.live.ask.submitting = true;
      state.live.status = "已提交选择，继续处理…";
    }
    renderInspector();
    renderSession();
    try {
      const body =
        answers.length === 1
          ? { ask_id: ask.ask_id, raw: answers[0].raw }
          : { ask_id: ask.ask_id, answers };
      await api(`/api/sessions/${state.sessionId}/answer`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      const submitted = formatAskAnswersForDisplay(ask, answers);
      if (state.live) {
        state.live.submittedAnswers = submitted;
        if (state.live.ask?.ask_id === ask.ask_id) {
          state.live.ask = null;
        }
        state.live.status = "已提交选择，继续处理…";
      }
      if (state.inspector?.type === "ask") {
        state.inspector = null;
      }
      renderInspector();
      renderSession();
      // 刷新导致 SSE 断开时：继续轮询会话直到计划/回合更新
      if (!state.streaming) {
        void watchSessionUntilSettled();
      }
    } catch (err) {
      ask.submitting = false;
      if (state.live?.ask?.ask_id === ask.ask_id) {
        state.live.ask.submitting = false;
      }
      renderInspector();
      renderSession();
      alert(err.message);
    }
  }

  async function watchSessionUntilSettled() {
    if (!state.sessionId) return;
    setBusy(true, "已提交，等待 Agent 继续…");
    const prevTurns = state.session?.turn_count || 0;
    const prevPlan = JSON.stringify({
      phase: state.session?.plan?.phase || null,
      pending: state.session?.pending_plan || null,
    });
    for (let i = 0; i < 180; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      if (state.streaming) return;
      try {
        const s = await api(`/api/sessions/${state.sessionId}`);
        state.session = s;
        if (s.pending_ask?.ask_id) {
          syncPendingAskFromSession(s);
          renderSession();
          return;
        }
        const planChanged =
          JSON.stringify({
            phase: s.plan?.phase || null,
            pending: s.pending_plan || null,
          }) !== prevPlan;
        if ((s.turn_count || 0) > prevTurns || planChanged) {
          state.live = null;
          if (state.inspector?.type === "ask") {
            state.inspector = null;
            renderInspector();
          }
          renderSession();
          notifyRunFinished(s);
          setBusy(false);
          return;
        }
        // 无实质变化时不整页重绘，只刷新进行中状态文案
        if (state.live) {
          updateLiveStatusDom();
        }
      } catch {
        /* keep waiting */
      }
    }
    setBusy(false);
  }

  function pickLiveProposal(live) {
    const steps = live?.steps || [];
    for (let i = steps.length - 1; i >= 0; i--) {
      const fa = String(steps[i].final_answer || "").trim();
      if (fa) return fa;
    }
    const ctx = (live?.ask?.questions || [])
      .map((q) => String(q.context || "").trim())
      .find(Boolean);
    return ctx || "";
  }

  function sanitizeLiveThought(text) {
    let t = String(text || "").replace(/\r\n/g, "\n").trim();
    if (!t) return "";
    // 模型常把 Action / Action Input 粘进 Thought，聊天里只保留推理正文
    t = t.replace(
      /(?:^|\n)\s*(?:\*{0,2})Action(?:\s*Input)?\s*[:：][\s\S]*$/i,
      ""
    );
    t = t.replace(/^(?:\*{0,2})Thought\s*[:：]\s*/i, "");
    return t.trim();
  }

  function isAskOnlyStep(step) {
    const actions = step?.actions || [];
    return actions.length > 0 && actions.every((a) => a.action === "ask_user");
  }

  function renderLiveStep(step, { compactAsk = false } = {}) {
    const card = document.createElement("div");
    card.className = "live-step";
    const head = document.createElement("div");
    head.className = "live-step-head";
    head.textContent = `第 ${step.index} 步`;
    card.appendChild(head);

    const askOnly = isAskOnlyStep(step);
    // ask_user 的问卷已在右侧面板；Thought/JSON 默认不铺开
    const thought = sanitizeLiveThought(step.thought);
    if (thought && !(askOnly || compactAsk)) {
      const el = document.createElement("div");
      el.className = "live-thought";
      const max = 160;
      if (thought.length > max) {
        const short = document.createElement("div");
        short.textContent = thought.slice(0, max).trimEnd() + "…";
        el.appendChild(short);
        const more = document.createElement("details");
        more.className = "live-thought-more";
        const sum = document.createElement("summary");
        sum.textContent = "展开思考";
        more.appendChild(sum);
        const full = document.createElement("div");
        full.textContent = thought;
        more.appendChild(full);
        el.appendChild(more);
      } else {
        el.textContent = thought;
      }
      card.appendChild(el);
    }

    if (step.actions && step.actions.length) {
      const row = document.createElement("div");
      row.className = "live-actions";
      for (const a of step.actions) {
        const chip = document.createElement("span");
        chip.className = `action-chip${a.ok === false ? " err" : a.ok === true ? " ok" : ""}`;
        const kind = a.action === "ask_user" ? "询问" : a.action;
        const label = a.op ? `${kind}:${a.op}` : kind;
        const path = a.path || a.change?.path || "";
        chip.textContent =
          label +
          (path ? ` ${path}` : "") +
          (a.ok === false ? " ✗" : a.ok === true ? " ✓" : "");
        // ask_user 的 JSON 已在右侧；其它工具仍可用 title 预览参数
        if (a.input && a.action !== "ask_user") chip.title = a.input;
        row.appendChild(chip);
      }
      card.appendChild(row);
    }

    if (step.final_answer && !askOnly) {
      const fa = document.createElement("div");
      fa.className = "live-final";
      const cleaned = sanitizeLiveThought(step.final_answer);
      if (cleaned && !looksLikeChoicePrompt(cleaned)) {
        setMarkdown(fa, cleaned);
        card.appendChild(fa);
      }
    }
    return card;
  }

  function changeKey(prefix, ch) {
    return `${prefix}:${ch.kind || ""}:${ch.path || ""}:${ch.dest || ""}`;
  }

  function summarizeChangeStats(changes) {
    let add = 0;
    let del = 0;
    for (const ch of changes) {
      const s = diffLineStats(ch.diff || "");
      add += s.add;
      del += s.del;
    }
    if (!add && !del) return null;
    return { add, del };
  }

  function diffLineStats(diffText) {
    let add = 0;
    let del = 0;
    for (const line of String(diffText).split("\n")) {
      if (line.startsWith("+") && !line.startsWith("+++")) add += 1;
      else if (line.startsWith("-") && !line.startsWith("---")) del += 1;
    }
    return { add, del };
  }

  function kindLabel(kind) {
    const map = {
      write: "新建",
      overwrite: "覆盖",
      patch: "修改",
      move: "移动",
      read: "读取",
      list: "列表",
      file: "文件",
      action: "操作",
      local_file: "文件",
      shell_run: "终端",
      search_code: "搜索",
      git_ops: "Git",
      ask_user: "询问",
      feishu_notify: "飞书",
      request_capability: "能力",
    };
    return map[kind] || kind || "改动";
  }

  function currentWorkdir() {
    return String((state.session && state.session.workdir) || "").trim();
  }

  function isAbsPath(p) {
    const s = String(p || "");
    return /^[a-zA-Z]:[\\/]/.test(s) || s.startsWith("/") || s.startsWith("\\\\");
  }

  function joinWorkdirPath(relOrAbs) {
    const raw = String(relOrAbs || "").trim().replace(/\\/g, "/");
    if (!raw) return "";
    if (isAbsPath(raw)) return raw;
    const root = currentWorkdir().replace(/\\/g, "/").replace(/\/+$/, "");
    if (!root) return raw;
    return `${root}/${raw.replace(/^\/+/, "")}`;
  }

  function vscodeFileHref(absPath, line) {
    let p = String(absPath || "").replace(/\\/g, "/");
    if (!p) return "";
    // Unix 绝对路径需要 vscode://file//path
    let href;
    if (p.startsWith("/")) {
      href = `vscode://file${p}`;
    } else {
      href = `vscode://file/${p}`;
    }
    if (line && Number(line) > 0) href += `:${Number(line)}`;
    return href;
  }

  function renderFileLink(relOrAbs, { className = "change-path file-link", label } = {}) {
    const display = label || relOrAbs || "(unknown)";
    const abs = joinWorkdirPath(relOrAbs);
    const href = vscodeFileHref(abs);
    if (!href) {
      const span = document.createElement("span");
      span.className = className.replace(/\bfile-link\b/, "").trim() || "change-path";
      span.textContent = display;
      return span;
    }
    const a = document.createElement("a");
    a.className = className;
    a.href = href;
    a.textContent = display;
    a.title = `在 VS Code 中打开\n${abs}`;
    a.addEventListener("click", (e) => {
      e.stopPropagation();
    });
    return a;
  }

  function renderPathLabel(ch) {
    if (ch.kind === "move" && (ch.path || ch.dest)) {
      const wrap = document.createElement("span");
      wrap.className = "change-path-wrap";
      wrap.appendChild(renderFileLink(ch.path));
      const arrow = document.createElement("span");
      arrow.className = "path-arrow";
      arrow.textContent = " → ";
      wrap.appendChild(arrow);
      wrap.appendChild(renderFileLink(ch.dest || ch.path));
      return wrap;
    }
    return renderFileLink(ch.path || "");
  }

  function setPanelPathNode(node) {
    changePanelPath.replaceChildren();
    if (typeof node === "string") {
      changePanelPath.textContent = node;
    } else if (node) {
      changePanelPath.appendChild(node);
    }
  }

  function mergeChangesByPath(changes) {
    const map = new Map();
    const order = [];
    for (const ch of changes || []) {
      if (!ch || !ch.path) continue;
      const path = String(ch.path).replace(/\\/g, "/");
      const key =
        ch.kind === "move"
          ? `move|${path}|${String(ch.dest || "").replace(/\\/g, "/")}`
          : `file|${path}`;
      if (!map.has(key)) {
        map.set(key, {
          ...ch,
          path,
          patch_count: 1,
          _diffs: ch.diff ? [ch.diff] : [],
        });
        order.push(key);
        continue;
      }
      const cur = map.get(key);
      cur.patch_count = (cur.patch_count || 1) + 1;
      if (ch.diff) cur._diffs.push(ch.diff);
      if (ch.kind && cur.kind && ch.kind !== cur.kind) {
        if (["write", "overwrite", "patch"].includes(ch.kind)) cur.kind = "patch";
      }
      if (ch.new_text) cur.new_text = ch.new_text;
    }
    return order.map((k) => {
      const item = map.get(k);
      if (item._diffs && item._diffs.length) {
        item.diff = mergeClientDiffs(item.path, item._diffs);
      }
      delete item._diffs;
      return item;
    });
  }

  function mergeClientDiffs(path, diffs) {
    const bodies = [];
    const seen = new Set();
    for (const d of diffs) {
      const body = String(d || "")
        .split("\n")
        .filter((ln) => !ln.startsWith("--- ") && !ln.startsWith("+++ "))
        .join("\n")
        .replace(/^\n+|\n+$/g, "");
      if (!body || seen.has(body)) continue;
      seen.add(body);
      bodies.push(body);
    }
    if (!bodies.length) return "";
    return `--- a/${path}\n+++ b/${path}\n${bodies.join("\n")}\n`;
  }

  function renderChangeList(changes, prefix) {
    const merged = mergeChangesByPath(changes);
    const wrap = document.createElement("div");
    wrap.className = "code-changes";

    const head = document.createElement("div");
    head.className = "code-changes-summary static";
    const stats = summarizeChangeStats(merged);
    const multi = merged.reduce((n, c) => n + (c.patch_count > 1 ? c.patch_count - 1 : 0), 0);
    head.innerHTML = `<span class="code-changes-title">修改</span>
      <span class="code-changes-meta">${merged.length} 文件${
        multi ? ` · ${merged.reduce((n, c) => n + (c.patch_count || 1), 0)} 处` : ""
      }${stats ? ` · <span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>` : ""}</span>`;
    wrap.appendChild(head);

    const list = document.createElement("div");
    list.className = "code-changes-list";
    for (const ch of merged) {
      list.appendChild(renderChangeRow(ch, changeKey(prefix, ch)));
    }
    wrap.appendChild(list);
    return wrap;
  }

  function renderChangeRow(ch, key) {
    const row = document.createElement("div");
    row.className = `code-change-row${
      state.inspector?.type === "change" && state.inspector?.key === key ? " active" : ""
    }`;
    row.setAttribute("role", "button");
    row.tabIndex = 0;

    const badge = document.createElement("span");
    badge.className = `change-badge kind-${ch.kind || "patch"}`;
    badge.textContent = kindLabel(ch.kind);

    row.appendChild(badge);
    row.appendChild(renderPathLabel(ch));

    if (ch.patch_count > 1) {
      const count = document.createElement("span");
      count.className = "patch-count";
      count.textContent = `${ch.patch_count} 处`;
      row.appendChild(count);
    }

    if (ch.kind !== "move") {
      const stats = diffLineStats(ch.diff || "");
      if (stats.add || stats.del) {
        const meta = document.createElement("span");
        meta.className = "change-stats";
        meta.innerHTML = `<span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>`;
        row.appendChild(meta);
      }
    }

    const open = () => openChangePanel(ch, key);
    row.onclick = (e) => {
      if (e.target.closest("a.file-link")) return;
      open();
    };
    row.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        open();
      }
    };
    return row;
  }

  function openPlanPanel(plan) {
    state.inspector = {
      type: "plan",
      key: "pending-plan",
      plan,
      selectedStep: 0,
    };
    renderInspector();
    renderSession();
  }

  function openDetailPanel(text, key, title) {
    state.inspector = {
      type: "detail",
      key,
      text,
      title: title || "细节",
    };
    renderInspector();
    renderSession();
  }

  function clampInspectorWidth(px) {
    const shellWidth = shellEl.getBoundingClientRect().width || window.innerWidth;
    const sidebar =
      parseFloat(getComputedStyle(shellEl).getPropertyValue("--sidebar")) || 300;
    const handle =
      parseFloat(getComputedStyle(shellEl).getPropertyValue("--resize-handle")) || 5;
    const maxByLayout = Math.max(
      INSPECTOR_MIN,
      Math.floor(shellWidth - sidebar - handle - CHAT_MIN)
    );
    const max = Math.min(INSPECTOR_MAX, maxByLayout);
    return Math.max(INSPECTOR_MIN, Math.min(max, Math.round(px)));
  }

  function getInspectorWidth() {
    const raw = shellEl.style.getPropertyValue("--inspector") || "";
    const n = parseInt(raw, 10);
    if (Number.isFinite(n) && n > 0) return n;
    const stored = parseInt(localStorage.getItem(INSPECTOR_WIDTH_KEY) || "", 10);
    if (Number.isFinite(stored) && stored > 0) return stored;
    return 420;
  }

  function setInspectorWidth(px, { persist = true } = {}) {
    const width = clampInspectorWidth(px);
    shellEl.style.setProperty("--inspector", `${width}px`);
    if (inputInspectorWidth) inputInspectorWidth.value = String(width);
    if (persist) {
      try {
        localStorage.setItem(INSPECTOR_WIDTH_KEY, String(width));
      } catch {
        /* ignore quota */
      }
    }
    return width;
  }

  function initInspectorResize() {
    const saved = parseInt(localStorage.getItem(INSPECTOR_WIDTH_KEY) || "", 10);
    setInspectorWidth(Number.isFinite(saved) ? saved : 420, { persist: false });

    if (inputInspectorWidth) {
      inputInspectorWidth.min = String(INSPECTOR_MIN);
      inputInspectorWidth.max = String(INSPECTOR_MAX);
      inputInspectorWidth.addEventListener("change", () => {
        const n = parseInt(inputInspectorWidth.value, 10);
        if (Number.isFinite(n)) setInspectorWidth(n);
        else inputInspectorWidth.value = String(getInspectorWidth());
      });
      inputInspectorWidth.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          inputInspectorWidth.blur();
        }
      });
    }

    let dragging = false;
    let startX = 0;
    let startWidth = 0;

    const onMove = (e) => {
      if (!dragging) return;
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const delta = startX - clientX; // 向左拖 → 右侧变宽
      setInspectorWidth(startWidth + delta, { persist: false });
    };

    const onUp = () => {
      if (!dragging) return;
      dragging = false;
      shellEl.classList.remove("resizing-inspector");
      setInspectorWidth(getInspectorWidth(), { persist: true });
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("touchmove", onMove);
      window.removeEventListener("touchend", onUp);
    };

    const onDown = (e) => {
      if (changePanel.classList.contains("hidden")) return;
      dragging = true;
      startX = e.touches ? e.touches[0].clientX : e.clientX;
      startWidth = getInspectorWidth();
      shellEl.classList.add("resizing-inspector");
      e.preventDefault();
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("touchmove", onMove, { passive: false });
      window.addEventListener("touchend", onUp);
    };

    resizeInspector.addEventListener("pointerdown", onDown);
    resizeInspector.addEventListener("dblclick", () => setInspectorWidth(420));
    window.addEventListener("resize", () => {
      if (!changePanel.classList.contains("hidden")) {
        setInspectorWidth(getInspectorWidth(), { persist: true });
      }
    });
  }

  function openChangePanel(ch, key) {
    state.inspector = { type: "change", key, change: ch };
    renderInspector();
    renderSession();
  }

  function closeInspector() {
    state.inspector = null;
    renderInspector();
    renderSession();
    updateAskFallbackButton();
  }

  function renderInspector() {
    const insp = state.inspector;
    if (!insp) {
      changePanel.classList.add("hidden");
      resizeInspector.classList.add("hidden");
      shellEl.classList.remove("with-inspector");
      changePanelBody.innerHTML = "";
      changePanelKind.textContent = "";
      setPanelPathNode("");
      return;
    }

    changePanel.classList.remove("hidden");
    resizeInspector.classList.remove("hidden");
    shellEl.classList.add("with-inspector");
    setInspectorWidth(getInspectorWidth(), { persist: false });
    changePanelBody.innerHTML = "";

    if (insp.type === "plan") {
      renderPlanInspector(insp.plan);
      return;
    }
    if (insp.type === "ask") {
      const ask = state.live?.ask && state.live.ask.ask_id === insp.ask?.ask_id
        ? state.live.ask
        : insp.ask;
      if (!ask) {
        changePanelKind.textContent = "确认";
        changePanelKind.className = "change-panel-kind kind-ask";
        setPanelPathNode("待确认问题");
        const empty = document.createElement("div");
        empty.className = "change-panel-empty";
        empty.textContent = "没有待确认的问题。";
        changePanelBody.appendChild(empty);
        return;
      }
      insp.ask = ask;
      renderAskInspector(ask);
      return;
    }
    if (insp.type === "permission") {
      const perm =
        state.live?.permission &&
        state.live.permission.ask_id === insp.permission?.ask_id
          ? state.live.permission
          : insp.permission || state.session?.pending_permission;
      if (!perm?.ask_id) {
        changePanelKind.textContent = "授权";
        changePanelKind.className = "change-panel-kind kind-ask";
        setPanelPathNode("待授权");
        const empty = document.createElement("div");
        empty.className = "change-panel-empty";
        empty.textContent = "没有待处理的权限请求。";
        changePanelBody.appendChild(empty);
        return;
      }
      insp.permission = perm;
      renderPermissionInspector(perm);
      return;
    }
    if (insp.type === "detail") {
      changePanelKind.textContent = "细节";
      changePanelKind.className = "change-panel-kind kind-detail";
      setPanelPathNode(insp.title || "运行细节");
      const pre = document.createElement("pre");
      pre.className = "code-block plan-detail-text";
      pre.textContent = insp.text || "";
      changePanelBody.appendChild(pre);
      return;
    }

    const ch = insp.change;
    changePanelKind.textContent = kindLabel(ch.kind);
    changePanelKind.className = `change-panel-kind kind-${ch.kind || "patch"}`;
    setPanelPathNode(renderPathLabel(ch));

    if (ch.kind === "move") {
      const empty = document.createElement("div");
      empty.className = "change-panel-empty";
      empty.textContent = "已移动文件，无内容 diff。";
      changePanelBody.appendChild(empty);
      return;
    }

    const stats = diffLineStats(ch.diff || "");
    if (stats.add || stats.del) {
      const bar = document.createElement("div");
      bar.className = "change-panel-stats";
      bar.innerHTML = `<span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>`;
      changePanelBody.appendChild(bar);
    }

    const diffText = ch.diff || "";
    if (diffText) {
      changePanelBody.appendChild(renderDiffPre(diffText));
    } else if (ch.new_text) {
      const pre = document.createElement("pre");
      pre.className = "code-block";
      pre.textContent = ch.new_text;
      changePanelBody.appendChild(pre);
    } else {
      const empty = document.createElement("div");
      empty.className = "change-panel-empty";
      empty.textContent = "暂无改动内容。";
      changePanelBody.appendChild(empty);
    }
  }

  function parsePlanStep(step) {
    let args = step.arguments;
    if (!args || typeof args !== "object") {
      try {
        args = JSON.parse(step.raw_input || "{}");
      } catch {
        args = {};
      }
    }
    if (typeof args !== "object" || args === null) args = {};

    const skill = String(step.skill || step.action || "step");
    const op = String(args.action || "").toLowerCase();
    const path = String(args.path || "").replace(/\\/g, "/");
    const dest = String(args.dest || "").replace(/\\/g, "/");
    const oldText = args.old_text != null ? String(args.old_text) : "";
    const newText =
      args.new_text != null
        ? String(args.new_text)
        : args.content != null
          ? String(args.content)
          : "";

    let kind = "action";
    if (skill === "local_file") {
      if (op === "write") kind = "write";
      else if (op === "patch") kind = "patch";
      else if (op === "move") kind = "move";
      else if (op === "read") kind = "read";
      else if (op === "list") kind = "list";
      else kind = op || "file";
    }

    const diff =
      kind === "patch" || kind === "write"
        ? buildClientUnifiedDiff(path || "file", kind === "write" ? "" : oldText, newText)
        : "";

    return {
      index: step.index,
      skill,
      op,
      kind,
      path,
      dest,
      why: String(step.why || ""),
      oldText,
      newText,
      diff,
      args,
    };
  }

  function buildClientUnifiedDiff(path, oldText, newText) {
    const a = String(oldText || "").split("\n");
    const b = String(newText || "").split("\n");
    if (a.length === 1 && a[0] === "" && b.length === 1 && b[0] === "") return "";
    if (a.join("\n") === b.join("\n")) return "";

    // 简易 LCS 行 diff，足够展示计划预览
    const n = a.length;
    const m = b.length;
    const dp = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const lines = [`--- a/${path}`, `+++ b/${path}`, "@@"];
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j]) {
        lines.push(` ${a[i]}`);
        i += 1;
        j += 1;
      } else if (dp[i + 1][j] >= dp[i][j + 1]) {
        lines.push(`-${a[i]}`);
        i += 1;
      } else {
        lines.push(`+${b[j]}`);
        j += 1;
      }
    }
    while (i < n) {
      lines.push(`-${a[i++]}`);
    }
    while (j < m) {
      lines.push(`+${b[j++]}`);
    }
    return lines.join("\n") + "\n";
  }

  function planStepTitle(parsed) {
    if (parsed.kind === "move" && parsed.path) {
      return `${parsed.path} → ${parsed.dest || "?"}`;
    }
    if (parsed.path) return parsed.path;
    if (parsed.skill === "local_file" && parsed.op) {
      return `local_file:${parsed.op}`;
    }
    return parsed.skill;
  }

  function mergeParsedPlanSteps(parsedSteps) {
    const out = [];
    const fileIndex = new Map();
    for (const step of parsedSteps || []) {
      const isFile = ["write", "patch", "move", "overwrite"].includes(step.kind);
      if (!isFile || !step.path) {
        out.push(step);
        continue;
      }
      const key =
        step.kind === "move"
          ? `move|${step.path}|${step.dest || ""}`
          : `file|${step.path}`;
      if (!fileIndex.has(key)) {
        const merged = {
          ...step,
          patch_count: 1,
          whys: step.why ? [step.why] : [],
          _diffs: step.diff ? [step.diff] : [],
        };
        fileIndex.set(key, merged);
        out.push(merged);
        continue;
      }
      const cur = fileIndex.get(key);
      cur.patch_count = (cur.patch_count || 1) + 1;
      if (step.why && !cur.whys.includes(step.why)) cur.whys.push(step.why);
      if (step.diff) cur._diffs.push(step.diff);
      if (step.kind !== cur.kind) cur.kind = "patch";
      if (step.newText) cur.newText = step.newText;
      cur.why = cur.whys.join("；");
      cur.diff = mergeClientDiffs(cur.path, cur._diffs);
    }
    for (const step of out) {
      delete step._diffs;
      delete step.whys;
    }
    return out;
  }

  function renderPlanInspector(plan) {
    const parsedSteps = mergeParsedPlanSteps((plan.steps || []).map(parsePlanStep));
    const fileSteps = parsedSteps.filter((s) =>
      ["write", "patch", "move", "overwrite"].includes(s.kind)
    );
    const patchTotal = fileSteps.reduce((n, s) => n + (s.patch_count || 1), 0);

    changePanelKind.textContent = "计划";
    changePanelKind.className = "change-panel-kind kind-plan";
    setPanelPathNode(
      parsedSteps.length > 0
        ? `${fileSteps.length || parsedSteps.length} 文件${
            patchTotal > fileSteps.length ? ` · ${patchTotal} 处改动` : ""
          }`
        : "待确认计划"
    );

    const wrap = document.createElement("div");
    wrap.className = "plan-inspector";

    if (plan.summary) {
      const summary = document.createElement("div");
      summary.className = "plan-inspector-summary md-body";
      const cleaned = cleanPlanSummaryText(plan.summary);
      setMarkdown(summary, cleaned);
      wrap.appendChild(summary);
    }

    if (!parsedSteps.length) {
      const text = plan.text || "";
      if (text) {
        const pre = document.createElement("pre");
        pre.className = "code-block plan-detail-text";
        pre.textContent = text;
        wrap.appendChild(pre);
      } else {
        const empty = document.createElement("div");
        empty.className = "change-panel-empty";
        empty.textContent = "暂无计划细节。";
        wrap.appendChild(empty);
      }
      changePanelBody.appendChild(wrap);
      return;
    }

    let selected = Number(state.inspector?.selectedStep ?? 0);
    if (selected < 0 || selected >= parsedSteps.length) selected = 0;

    const nav = document.createElement("div");
    nav.className = "plan-step-nav";
    parsedSteps.forEach((step, idx) => {
      const btn = document.createElement("div");
      btn.className = `plan-step-nav-item${idx === selected ? " active" : ""}`;
      btn.setAttribute("role", "button");
      btn.tabIndex = 0;
      const stats = diffLineStats(step.diff || "");
      const badge = kindLabel(step.kind === "action" ? step.skill : step.kind);

      const idxEl = document.createElement("span");
      idxEl.className = "plan-step-idx";
      idxEl.textContent = String(idx + 1);
      const badgeEl = document.createElement("span");
      badgeEl.className = `change-badge kind-${step.kind}`;
      badgeEl.textContent = badge;
      btn.appendChild(idxEl);
      btn.appendChild(badgeEl);

      if (step.path || step.dest) {
        btn.appendChild(
          step.kind === "move"
            ? renderPathLabel(step)
            : renderFileLink(step.path, { label: planStepTitle(step) })
        );
      } else {
        const pathEl = document.createElement("span");
        pathEl.className = "change-path";
        pathEl.textContent = planStepTitle(step);
        btn.appendChild(pathEl);
      }

      if (step.patch_count > 1) {
        const count = document.createElement("span");
        count.className = "patch-count";
        count.textContent = `${step.patch_count} 处`;
        btn.appendChild(count);
      }

      if (stats.add || stats.del) {
        const meta = document.createElement("span");
        meta.className = "change-stats";
        meta.innerHTML = `<span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>`;
        btn.appendChild(meta);
      }

      const select = () => {
        if (state.inspector?.type === "plan") {
          state.inspector.selectedStep = idx;
          renderInspector();
        }
      };
      btn.onclick = (e) => {
        if (e.target.closest("a.file-link")) return;
        select();
      };
      btn.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          select();
        }
      };
      nav.appendChild(btn);
    });
    wrap.appendChild(nav);

    const detail = document.createElement("div");
    detail.className = "plan-step-detail";
    detail.appendChild(renderPlanStepDetail(parsedSteps[selected]));
    wrap.appendChild(detail);

    changePanelBody.appendChild(wrap);
  }

  function renderPlanStepDetail(step) {
    const box = document.createElement("div");
    box.className = "plan-step-detail-inner";

    const head = document.createElement("div");
    head.className = "plan-step-detail-head";
    const badgeEl = document.createElement("span");
    badgeEl.className = `change-badge kind-${step.kind}`;
    badgeEl.textContent = kindLabel(step.kind === "action" ? step.skill : step.kind);
    head.appendChild(badgeEl);
    if (step.path || step.dest) {
      head.appendChild(
        step.kind === "move"
          ? renderPathLabel(step)
          : renderFileLink(step.path, { label: planStepTitle(step) })
      );
    } else {
      const pathEl = document.createElement("span");
      pathEl.className = "change-path";
      pathEl.textContent = planStepTitle(step);
      head.appendChild(pathEl);
    }
    box.appendChild(head);

    if (step.why) {
      const why = document.createElement("div");
      why.className = "plan-step-why";
      why.textContent = step.why;
      box.appendChild(why);
    }

    if (step.kind === "move") {
      const move = document.createElement("div");
      move.className = "plan-step-move";
      move.appendChild(renderPathLabel(step));
      box.appendChild(move);
      return box;
    }

    if (step.diff) {
      const stats = diffLineStats(step.diff);
      if (stats.add || stats.del) {
        const bar = document.createElement("div");
        bar.className = "change-panel-stats";
        bar.innerHTML = `<span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>`;
        box.appendChild(bar);
      }
      box.appendChild(renderDiffPre(step.diff));
      return box;
    }

    if (step.kind === "write" && step.newText) {
      const label = document.createElement("div");
      label.className = "plan-step-section-label";
      label.textContent = "将写入";
      box.appendChild(label);
      const pre = document.createElement("pre");
      pre.className = "code-block";
      pre.textContent = step.newText;
      box.appendChild(pre);
      return box;
    }

    if (step.kind === "read" || step.kind === "list") {
      const info = document.createElement("div");
      info.className = "plan-step-meta";
      info.appendChild(document.createTextNode(step.kind === "read" ? "读取 " : "列出 "));
      info.appendChild(renderFileLink(step.path || (step.kind === "list" ? "." : "")));
      box.appendChild(info);
      return box;
    }

    // 其它 skill：可读键值，不甩整段 JSON
    const keys = Object.keys(step.args || {}).filter((k) => k !== "action");
    if (!keys.length) {
      const empty = document.createElement("div");
      empty.className = "change-panel-empty";
      empty.textContent = "无额外参数";
      box.appendChild(empty);
      return box;
    }
    const dl = document.createElement("div");
    dl.className = "plan-arg-list";
    for (const k of keys) {
      const row = document.createElement("div");
      row.className = "plan-arg-row";
      const keyEl = document.createElement("div");
      keyEl.className = "plan-arg-key";
      keyEl.textContent = k;
      const valEl = document.createElement("pre");
      valEl.className = "plan-arg-val";
      const v = step.args[k];
      valEl.textContent = typeof v === "string" ? v : JSON.stringify(v, null, 2);
      row.appendChild(keyEl);
      row.appendChild(valEl);
      dl.appendChild(row);
    }
    box.appendChild(dl);
    return box;
  }

  function parseHunkHeader(line) {
    // @@ -91,13 +91,17 @@ optional context
    const m = String(line).match(/@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@/);
    if (!m) return null;
    return { oldStart: Number(m[1]), newStart: Number(m[2]) };
  }

  function renderDiffPre(diffText) {
    const pre = document.createElement("pre");
    pre.className = "diff-block";
    const lines = String(diffText).split("\n");
    let hunkIndex = 0;
    let oldLine = 0;
    let newLine = 0;
    let hasLineNums = false;

    for (const line of lines) {
      // 文件头已在面板标题展示，这里跳过避免重复噪音
      if (line.startsWith("--- ") || line.startsWith("+++ ")) {
        continue;
      }
      // @@ 表示跳过了中间未改动的代码，不是源码断行
      if (line.startsWith("@@")) {
        hunkIndex += 1;
        const meta = parseHunkHeader(line);
        if (meta) {
          oldLine = meta.oldStart;
          newLine = meta.newStart;
          hasLineNums = true;
        } else {
          oldLine = 1;
          newLine = 1;
          hasLineNums = false;
        }
        const sep = document.createElement("span");
        sep.className = "diff-line diff-hunk-sep";
        sep.title = line;
        if (hunkIndex === 1) {
          sep.textContent = meta
            ? `—— 第 ${hunkIndex} 处改动 · 约第 ${meta.newStart} 行 ——`
            : `—— 第 ${hunkIndex} 处改动 ——`;
        } else {
          sep.textContent = meta
            ? `⋯ 中间有未改动代码，已省略 · 第 ${hunkIndex} 处 · 约第 ${meta.newStart} 行 ⋯`
            : `⋯ 中间有未改动代码，已省略 · 第 ${hunkIndex} 处 ⋯`;
        }
        pre.appendChild(sep);
        continue;
      }

      // 忽略空尾行
      if (line === "" && oldLine === 0 && newLine === 0) continue;

      const isAdd = line.startsWith("+");
      const isDel = line.startsWith("-");
      // unified diff 上下文以空格开头；兼容无前缀
      const isCtx = !isAdd && !isDel;

      let lnOld = "";
      let lnNew = "";
      if (hasLineNums) {
        if (isDel) {
          lnOld = String(oldLine++);
        } else if (isAdd) {
          lnNew = String(newLine++);
        } else {
          lnOld = String(oldLine++);
          lnNew = String(newLine++);
        }
      } else if (hunkIndex > 0) {
        // 无绝对行号时（片段 diff）仍给相对行号便于对照
        if (isDel) {
          lnOld = String(oldLine++);
        } else if (isAdd) {
          lnNew = String(newLine++);
        } else {
          lnOld = String(oldLine++);
          lnNew = String(newLine++);
        }
      }

      const row = document.createElement("span");
      row.className = "diff-line";
      if (isAdd) row.classList.add("diff-add");
      else if (isDel) row.classList.add("diff-del");
      else row.classList.add("diff-ctx");

      const gutter = document.createElement("span");
      gutter.className = "diff-gutter";
      const oldEl = document.createElement("span");
      oldEl.className = "diff-ln diff-ln-old";
      oldEl.textContent = lnOld;
      const newEl = document.createElement("span");
      newEl.className = "diff-ln diff-ln-new";
      newEl.textContent = lnNew;
      gutter.appendChild(oldEl);
      gutter.appendChild(newEl);

      const code = document.createElement("span");
      code.className = "diff-code";
      // 去掉 diff 前缀符号，行号已表达增删；保留符号便于扫读
      code.textContent = line || " ";

      row.appendChild(gutter);
      row.appendChild(code);
      pre.appendChild(row);
    }
    return pre;
  }

  function applyAskUserEvent(event, { notify = true } = {}) {
    if (!event?.ask_id) return;
    if (!state.live) {
      state.live = emptyLive({ status: "等待你的选择…" });
    }
    const isNewAsk = !state.live.ask || state.live.ask.ask_id !== event.ask_id;
    const questions = normalizeAskQuestions(event);
    state.live.ask = {
      ask_id: event.ask_id,
      question: event.question || questions[0]?.question || "",
      options: event.options || questions[0]?.options || [],
      questions,
      submitting: false,
    };
    const n = questions.length;
    state.live.status =
      event.message || (n > 1 ? `等待你确认 ${n} 个问题…` : "等待你的选择…");
    if (isNewAsk) {
      if (notify) playNotify("attention");
      openAskInspector(state.live.ask);
    } else if (state.inspector?.type !== "ask") {
      openAskInspector(state.live.ask);
    } else {
      updateAskFallbackButton();
    }
  }

  function syncPendingAskFromSession(session) {
    const event = session?.pending_ask;
    if (!event?.ask_id) return false;
    if (state.live?.ask?.ask_id === event.ask_id) {
      if (state.inspector?.type !== "ask") openAskInspector(state.live.ask);
      return true;
    }
    applyAskUserEvent(event, { notify: true });
    // 运行线程仍在等待作答：禁止再发消息以免锁死
    setBusy(true, state.live?.status || "等待你的选择…");
    return true;
  }

  function updateLiveStatusDom() {
    const el = messagesEl.querySelector(".live-run .live-status-text");
    if (el && state.live) {
      el.textContent = state.live.status || "处理中…";
      return true;
    }
    return false;
  }

  function updateLiveDraftDom() {
    if (!state.live) return false;
    const root = messagesEl.querySelector(".bubble.live-run");
    if (!root) return false;
    let draftEl = root.querySelector(".live-draft");
    const text = state.live.draft || "";
    if (!text) {
      if (draftEl) draftEl.remove();
      return true;
    }
    if (!draftEl) {
      draftEl = document.createElement("div");
      draftEl.className = "live-draft";
      const status = root.querySelector(".live-status");
      const skills = root.querySelector(".live-skills");
      const anchor = skills || status;
      if (anchor && anchor.nextSibling) {
        root.insertBefore(draftEl, anchor.nextSibling);
      } else if (anchor) {
        anchor.after(draftEl);
      } else {
        root.appendChild(draftEl);
      }
    }
    draftEl.textContent = text;
    const nearBottom =
      messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 120;
    if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
    return true;
  }

  function patchLivePanelDom() {
    if (!state.live) return false;
    const existing = messagesEl.querySelector(".bubble.live-run");
    if (!existing) return false;
    const nearBottom =
      messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < 80;
    const next = renderLivePanel(state.live);
    existing.replaceWith(next);
    if (nearBottom) messagesEl.scrollTop = messagesEl.scrollHeight;
    updateAskFallbackButton();
    return true;
  }

  function applyLiveEvent(event) {
    if (event.type === "ask_user") {
      applyAskUserEvent(event, { notify: true });
      renderSession();
      return;
    }
    if (event.type === "permission_ask") {
      applyPermissionAskEvent(event);
      renderSession();
      return;
    }
    if (!state.live) return;
    if (event.type === "status") {
      const next = event.message || state.live.status;
      if (next === state.live.status) return;
      state.live.status = next;
      // 思考中的 status 很频繁时，只改文字，避免整页重绘闪烁
      if (!updateLiveStatusDom()) renderSession();
      return;
    }
    if (event.type === "assistant_delta") {
      const delta = event.delta || "";
      if (!delta) return;
      // 新一步开始流式时清空上一轮草稿
      if (
        event.step != null &&
        state.live._draftStep != null &&
        event.step !== state.live._draftStep
      ) {
        state.live.draft = "";
      }
      if (event.step != null) state.live._draftStep = event.step;
      state.live.draft = (state.live.draft || "") + delta;
      if (!updateLiveDraftDom()) {
        if (!patchLivePanelDom()) renderSession();
      }
      return;
    }
    if (event.type === "skill") {
      const name = event.skill || "?";
      const st = event.status || "start";
      if (!Array.isArray(state.live.skills)) state.live.skills = [];
      const idx = state.live.skills.findIndex(
        (s) => s.skill === name && (s.status === "start" || s.status === "running")
      );
      if (st === "start") {
        state.live.skills.push({ skill: name, status: "start" });
        if (!state.live.ask) {
          state.live.status = event.message || `执行 ${name}…`;
        }
      } else if (st === "end" || st === "update") {
        const target =
          idx >= 0
            ? state.live.skills[idx]
            : { skill: name, status: "start" };
        if (idx < 0) state.live.skills.push(target);
        if (st === "end") {
          target.status = event.ok === false ? "err" : "ok";
        }
      }
      // 只保留最近若干 chip，避免刷屏
      if (state.live.skills.length > 12) {
        state.live.skills = state.live.skills.slice(-12);
      }
      if (!patchLivePanelDom()) renderSession();
      else updateLiveStatusDom();
      return;
    }
    if (event.type === "cancelled") {
      state.live.cancelled = true;
      state.live.status = event.message || "已取消本轮任务";
      state.live.draft = "";
      if (!patchLivePanelDom()) renderSession();
      setBusy(true, "正在停止…");
      return;
    }
    if (event.type === "step") {
      // 完整 step 到达后清空流式草稿（已并入 timeline）
      state.live.draft = "";
      state.live._draftStep = null;
      const idx = state.live.steps.findIndex((s) => s.index === event.index);
      const item = {
        index: event.index,
        thought: event.thought || "",
        actions: event.actions || [],
        changes: event.changes || [],
        final_answer: event.final_answer || null,
        text: event.text || "",
      };
      if (idx >= 0) state.live.steps[idx] = item;
      else state.live.steps.push(item);
      if (event.todos && Array.isArray(event.todos.items)) {
        setTodos(event.todos);
        if (state.session) state.session.todos = event.todos;
      }
      const n = (event.changes || []).length;
      if (!state.live.ask) {
        state.live.status = event.final_answer
          ? "正在整理最终回答…"
          : n
            ? `已完成第 ${event.index} 步 · ${n} 处文件改动`
            : `已完成第 ${event.index} 步`;
      }
      // 只替换进行中气泡，不动历史消息
      if (!patchLivePanelDom()) renderSession();
      return;
    }
    renderSession();
  }

  function normalizeMarkdownSource(text) {
    return String(text ?? "")
      .replace(/\r\n/g, "\n")
      .replace(/[ \t\u00a0\u3000]+\n/g, "\n")
      .replace(/\n[ \t\u00a0\u3000]+\n/g, "\n\n")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  function renderMarkdown(text) {
    const raw = normalizeMarkdownSource(text);
    if (!raw) return "";
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
      // breaks:false：仅空行分段，避免“软换行 + 段落边距”叠出过大空隙
      const html = marked.parse(raw, { async: false, gfm: true, breaks: false });
      return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
    }
    return escapeHtml(raw).replace(/\n/g, "<br>");
  }

  function setMarkdown(el, text) {
    el.classList.add("md-body");
    el.innerHTML = renderMarkdown(text);
  }

  function bubble(role, text) {
    const div = document.createElement("div");
    div.className = `bubble ${role}`;
    div.innerHTML = `<div class="role">${role === "user" ? "你" : "助手"}</div><div class="body"></div>`;
    setMarkdown(div.querySelector(".body"), text);
    return div;
  }

  function findJsonObjectEnd(text, start = 0) {
    if (!text || text[start] !== "{") return -1;
    let depth = 0;
    let inStr = false;
    let esc = false;
    for (let i = start; i < text.length; i++) {
      const ch = text[i];
      if (inStr) {
        if (esc) {
          esc = false;
          continue;
        }
        if (ch === "\\") {
          esc = true;
          continue;
        }
        if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') {
        inStr = true;
        continue;
      }
      if (ch === "{") depth += 1;
      else if (ch === "}") {
        depth -= 1;
        if (depth === 0) return i;
      }
    }
    return -1;
  }

  function looksLikeChoicePrompt(text) {
    const s = String(text || "");
    if (!s.trim()) return false;
    if (/请回复你的选择|请回复.*选择|请选择下列|请从下列方案/.test(s)) return true;
    const rows = (s.match(/^\s*\|.+\|\s*$/gm) || []).length;
    if (rows >= 4 && /方案\s*[ABC]|\|\s*A\s*[\|：:]/.test(s)) return true;
    if (rows >= 4 && /问题\s*\d+/.test(s) && /\|\s*A\s*\|/.test(s)) return true;
    return false;
  }

  function cleanPlanSummaryText(text) {
    let s = String(text || "").trim();
    if (!s) return s;
    if (looksLikeChoicePrompt(s)) {
      return "计划已就绪，请确认后执行。";
    }
    const planAt = s.search(/\bPlan\s*:/i);
    if (planAt >= 0 && /skill\s*=/i.test(s.slice(planAt))) {
      s = s.slice(0, planAt).replace(/[：:\s]+$/g, "").trim();
    }
    const stepAt = s.search(/\d+\.\s*skill\s*=/i);
    if (stepAt >= 0 && /input\s*=/i.test(s.slice(stepAt))) {
      s = s.slice(0, stepAt).replace(/[：:\s]+$/g, "").trim();
    }
    if (/skill\s*=\s*\w+\s*\|\s*input\s*=/i.test(s)) {
      return "计划已就绪，请确认后执行。";
    }
    if (looksLikeChoicePrompt(s)) {
      return "计划已就绪，请确认后执行。";
    }
    return s;
  }

  function extractAnswerPlan(text) {
    const raw = String(text || "");
    if (!/skill\s*=/i.test(raw) || !/input\s*=/i.test(raw)) return null;

    let prose = raw;
    let body = raw;
    const planAt = raw.search(/\bPlan\s*:/i);
    if (planAt >= 0) {
      prose = raw.slice(0, planAt).replace(/[：:\s]+$/g, "").trim();
      body = raw.slice(planAt).replace(/^\s*Plan\s*:\s*/i, "");
    } else {
      const stepAt = raw.search(/\d+\.\s*skill\s*=/i);
      if (stepAt > 0) {
        prose = raw.slice(0, stepAt).replace(/[：:\s]+$/g, "").trim();
        body = raw.slice(stepAt);
      }
    }

    const marker = /(\d+)\.\s*skill\s*=\s*([^\s|]+)\s*\|\s*input\s*=\s*/gi;
    const marks = [];
    let m;
    while ((m = marker.exec(body)) !== null) {
      marks.push({
        index: m.index,
        num: Number(m[1]),
        skill: m[2],
        inputStart: m.index + m[0].length,
      });
    }
    if (!marks.length) return null;

    const steps = [];
    for (let i = 0; i < marks.length; i++) {
      const start = marks[i].inputStart;
      const end = i + 1 < marks.length ? marks[i + 1].index : body.length;
      const chunk = body.slice(start, end).trim();
      let rawInput = chunk;
      let why = "";
      if (chunk.startsWith("{")) {
        const je = findJsonObjectEnd(chunk, 0);
        if (je >= 0) {
          rawInput = chunk.slice(0, je + 1);
          const rest = chunk.slice(je + 1);
          const whyM = rest.match(/\|\s*why\s*=\s*([\s\S]*?)$/i);
          if (whyM) why = whyM[1].trim().replace(/^["']|["']$/g, "");
        }
      } else {
        const whyM = chunk.match(/\|\s*why\s*=\s*([\s\S]*?)$/i);
        if (whyM) {
          rawInput = chunk.slice(0, whyM.index).trim();
          why = whyM[1].trim().replace(/^["']|["']$/g, "");
        }
      }
      let args = {};
      try {
        args = JSON.parse(rawInput);
        if (!args || typeof args !== "object") args = {};
      } catch {
        args = {};
      }
      steps.push({
        index: marks[i].num,
        skill: marks[i].skill,
        arguments: args,
        why,
        raw_input: rawInput,
      });
    }

    return {
      prose: cleanPlanSummaryText(prose),
      steps,
    };
  }

  function renderInlinePlanPreview(parsedSteps, plan) {
    const wrap = document.createElement("div");
    wrap.className = "inline-plan";

    const head = document.createElement("div");
    head.className = "inline-plan-head";
    const fileN = parsedSteps.filter((s) =>
      ["write", "patch", "move", "overwrite"].includes(s.kind)
    ).length;
    const patchN = parsedSteps.reduce((n, s) => n + (s.patch_count || 1), 0);
    head.innerHTML = `<span class="inline-plan-title">计划步骤</span>
      <span class="inline-plan-meta">${parsedSteps.length} 步${
        fileN ? ` · ${fileN} 文件` : ""
      }${patchN > parsedSteps.length ? ` · ${patchN} 处改动` : ""}</span>`;
    wrap.appendChild(head);

    const list = document.createElement("div");
    list.className = "inline-plan-list";
    parsedSteps.forEach((step, idx) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = `inline-plan-row${
        state.inspector?.type === "plan" && state.inspector?.selectedStep === idx
          ? " active"
          : ""
      }`;
      const stats = diffLineStats(step.diff || "");
      const badge = document.createElement("span");
      badge.className = `change-badge kind-${step.kind}`;
      badge.textContent = kindLabel(step.kind === "action" ? step.skill : step.kind);
      row.appendChild(badge);

      const main = document.createElement("div");
      main.className = "inline-plan-main";
      const title = document.createElement("div");
      title.className = "inline-plan-path";
      title.textContent = planStepTitle(step);
      main.appendChild(title);
      if (step.why) {
        const why = document.createElement("div");
        why.className = "inline-plan-why";
        why.textContent = step.why;
        main.appendChild(why);
      }
      row.appendChild(main);

      const meta = document.createElement("div");
      meta.className = "inline-plan-side";
      if (step.patch_count > 1) {
        const count = document.createElement("span");
        count.className = "patch-count";
        count.textContent = `${step.patch_count} 处`;
        meta.appendChild(count);
      }
      if (stats.add || stats.del) {
        const st = document.createElement("span");
        st.className = "change-stats";
        st.innerHTML = `<span class="stat-add">+${stats.add}</span> <span class="stat-del">−${stats.del}</span>`;
        meta.appendChild(st);
      }
      row.appendChild(meta);

      row.onclick = () => {
        openPlanPanel(plan);
        if (state.inspector?.type === "plan") {
          state.inspector.selectedStep = idx;
          renderInspector();
        }
      };
      list.appendChild(row);
    });
    wrap.appendChild(list);
    return wrap;
  }

  function renderAssistantBubble(answer, { plan = null } = {}) {
    const div = document.createElement("div");
    div.className = "bubble assistant";
    div.innerHTML = `<div class="role">助手</div><div class="body"></div>`;
    const body = div.querySelector(".body");

    const extracted = extractAnswerPlan(answer);
    let steps = Array.isArray(plan?.steps) ? plan.steps : [];
    const hasPlan = Boolean(plan?.ok && (steps.length || extracted?.steps?.length));

    // 有待确认计划时，优先用计划摘要；丢掉「请回复选择」问卷表，避免和确认执行叠在一起
    let prose = "";
    if (hasPlan) {
      prose = cleanPlanSummaryText(plan?.summary || "");
      if (!prose || looksLikeChoicePrompt(prose) || looksLikeChoicePrompt(answer)) {
        prose =
          cleanPlanSummaryText(extracted?.prose || "") ||
          "计划已就绪，请确认后执行。";
      }
      if (looksLikeChoicePrompt(prose)) {
        prose = "计划已就绪，请确认后执行。";
      }
    } else {
      prose = cleanPlanSummaryText(answer || "");
      if (extracted?.prose) prose = extracted.prose || prose;
    }

    if (extracted?.steps?.length && !steps.length) {
      steps = extracted.steps;
    }

    if (!prose) {
      prose = steps.length
        ? "计划已就绪，请确认后执行。"
        : looksLikeChoicePrompt(answer)
          ? "请在右侧确认面板完成选择。"
          : answer || "(无回复)";
    }

    // 无结构化计划、且整段都是问卷时：不渲染大表，引导去右侧 ask 面板
    if (!steps.length && looksLikeChoicePrompt(answer)) {
      setMarkdown(body, "有多个方案待确认，请在右侧面板选择（勿在对话里回复编号）。");
      return div;
    }

    setMarkdown(body, prose);

    if (steps.length) {
      const parsed = mergeParsedPlanSteps(steps.map(parsePlanStep));
      const planObj = plan && plan.ok
        ? { ...plan, summary: prose, steps: plan.steps?.length ? plan.steps : steps }
        : { ok: true, summary: prose, steps, text: "" };
      div.appendChild(renderInlinePlanPreview(parsed, planObj));
    }

    return div;
  }

  async function refreshWorkspaces() {
    state.workspaces = await api("/api/workspaces");
    if (state.workspaceId && !state.workspaces.some((w) => w.id === state.workspaceId)) {
      state.workspaceId = null;
      state.sessionId = null;
      state.session = null;
    }
    if (state.sessionId && !findSessionMeta(state.sessionId)) {
      // may be filtered out of list only if archived and somehow missing — keep open
    }
    renderTree();
  }

  function applyWorkspaceUpdate(updated) {
    const idx = state.workspaces.findIndex((w) => w.id === updated.id);
    if (idx >= 0) {
      state.workspaces[idx] = { ...state.workspaces[idx], ...updated };
    }
  }

  async function patchWorkspace(id, patch) {
    const updated = await api(`/api/workspaces/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    });
    applyWorkspaceUpdate(updated);
    return updated;
  }

  async function toggleWorkspaceCollapsed(id, collapsed) {
    try {
      await patchWorkspace(id, { collapsed });
      state.workspaceId = id;
      renderTree();
    } catch (err) {
      alert(err.message);
    }
  }

  function startRenameWorkspace(ws, headEl) {
    const info = headEl.querySelector(".ws-info");
    if (!info || info.querySelector(".ws-alias-input")) return;

    const titleEl = info.querySelector(".title");
    const metaEl = info.querySelector(".meta");
    const prev = workspaceAlias(ws);
    const input = document.createElement("input");
    input.type = "text";
    input.className = "ws-alias-input";
    input.value = prev;
    input.maxLength = 64;
    input.setAttribute("aria-label", "工作区别名");
    titleEl.replaceWith(input);
    if (metaEl) metaEl.classList.add("hidden");
    input.focus();
    input.select();

    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      const next = input.value.trim() || folderName(ws.path);
      if (!save || next === prev) {
        renderTree();
        return;
      }
      try {
        await patchWorkspace(ws.id, { title: next });
        renderTree();
      } catch (err) {
        alert(err.message);
        renderTree();
      }
    };

    input.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        finish(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        finish(false);
      }
    };
    input.onblur = () => finish(true);
  }

  async function patchSessionFlags(sessionId, flags) {
    try {
      const updated = await api(`/api/sessions/${sessionId}`, {
        method: "PATCH",
        body: JSON.stringify(flags),
      });
      if (state.sessionId === sessionId && state.session) {
        state.session = { ...state.session, ...updated };
      }
      await refreshWorkspaces();
      if (flags.archived === true && state.sessionId === sessionId && !state.showArchived) {
        // stay on session even if hidden from default list
        renderSession();
      }
    } catch (err) {
      alert(err.message);
    }
  }

  async function selectSession(id, workspaceId) {
    setBusy(true, "加载会话…");
    try {
      if (state.sessionId !== id) {
        state.inspector = null;
        state.live = null;
        renderInspector();
      }
      state.sessionId = id;
      if (workspaceId) state.workspaceId = workspaceId;
      state.session = await api(`/api/sessions/${id}`);
      renderTree();
      const waiting = syncPendingAskFromSession(state.session);
      renderSession();
      if (waiting) {
        // keep busy while agent waits for answers
        return;
      }
    } catch (err) {
      alert(err.message);
    } finally {
      if (!state.live?.ask) setBusy(false);
    }
  }

  async function createSession(workspaceId) {
    const wid = workspaceId || state.workspaceId;
    if (!wid) return;
    setBusy(true, "创建对话…");
    try {
      const created = await api(`/api/workspaces/${wid}/sessions`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      state.workspaceId = created.workspace_id || wid;
      // ensure workspace expanded
      const ws = state.workspaces.find((w) => w.id === state.workspaceId);
      if (ws?.collapsed) {
        await api(`/api/workspaces/${state.workspaceId}`, {
          method: "PATCH",
          body: JSON.stringify({ collapsed: false }),
        });
      }
      await refreshWorkspaces();
      await selectSession(created.session_id, state.workspaceId);
    } catch (err) {
      alert(err.message);
      setBusy(false);
    }
  }

  $("btn-close-change").onclick = () => closeInspector();
  initInspectorResize();

  $("btn-add-ws").onclick = () => {
    formAddWs.classList.toggle("hidden");
    if (!formAddWs.classList.contains("hidden")) {
      (inputWsAlias || inputWsPath).focus();
    }
  };
  $("btn-cancel-ws").onclick = () => {
    formAddWs.classList.add("hidden");
    if (inputWsAlias) inputWsAlias.value = "";
    inputWsPath.value = "";
  };

  btnToggleArchived.onclick = () => {
    state.showArchived = !state.showArchived;
    renderTree();
  };

  formAddWs.onsubmit = async (e) => {
    e.preventDefault();
    const path = inputWsPath.value.trim();
    if (!path) return;
    const title = (inputWsAlias?.value || "").trim();
    setBusy(true, "添加工作目录…");
    try {
      const payload = { path };
      if (title) payload.title = title;
      const ws = await api("/api/workspaces", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      formAddWs.classList.add("hidden");
      if (inputWsAlias) inputWsAlias.value = "";
      inputWsPath.value = "";
      await refreshWorkspaces();
      state.workspaceId = ws.id;
      renderTree();
      renderSession();
    } catch (err) {
      alert(err.message);
    } finally {
      setBusy(false);
    }
  };

  btnDeleteSession.onclick = async () => {
    if (!state.sessionId) return;
    if (!confirm("删除当前对话？此操作不可撤销。")) return;
    setBusy(true, "删除中…");
    try {
      await api(`/api/sessions/${state.sessionId}`, { method: "DELETE" });
      state.sessionId = null;
      state.session = null;
      closeInspector();
      await refreshWorkspaces();
      renderSession();
    } catch (err) {
      alert(err.message);
    } finally {
      setBusy(false);
    }
  };

  document.querySelectorAll(".seg-btn").forEach((btn) => {
    btn.onclick = async () => {
      if (!state.sessionId || state.busy) return;
      const mode = btn.dataset.mode;
      setBusy(true, "切换模式…");
      try {
        state.session = await api(`/api/sessions/${state.sessionId}`, {
          method: "PATCH",
          body: JSON.stringify({ mode }),
        });
        renderSession();
      } catch (err) {
        alert(err.message);
      } finally {
        setBusy(false);
      }
    };
  });

  selectDetail.onchange = async () => {
    if (!state.sessionId || state.busy) return;
    setBusy(true, "更新细节级别…");
    try {
      state.session = await api(`/api/sessions/${state.sessionId}`, {
        method: "PATCH",
        body: JSON.stringify({ detail: selectDetail.value }),
      });
      renderSession();
    } catch (err) {
      alert(err.message);
    } finally {
      setBusy(false);
    }
  };

  async function confirmPlan() {
    if (!state.sessionId || state.busy) return;
    unlockAudio();
    setBusy(true, "执行计划…");
    state.streaming = true;
    if (state.inspector?.type === "plan") {
      state.inspector = null;
      renderInspector();
    }
    state.live = emptyLive({ status: "开始执行计划…" });
    renderSession();
    try {
      const result = await streamEvents(
        `/api/sessions/${state.sessionId}/confirm/stream`,
        {},
        applyLiveEvent
      );
      if (result.turns?.length && result.detail_text) {
        result.turns[result.turns.length - 1]._detail_text = result.detail_text;
      }
      state.live = null;
      state.session = result;
      await refreshWorkspaces();
      renderSession();
      notifyRunFinished(result);
    } catch (err) {
      if (state.live) {
        state.live.error = err.message;
        state.live.status = "执行失败";
        renderSession();
      }
      notifyRunFinished(null, { error: true });
      alert(err.message);
      state.live = null;
      renderSession();
    } finally {
      state.streaming = false;
      if (!state.live?.ask) setBusy(false);
    }
  }

  $("form-chat").onsubmit = async (e) => {
    e.preventDefault();
    if (!state.sessionId || state.busy) return;
    const message = inputMessage.value.trim();
    if (!message) return;
    inputMessage.value = "";
    unlockAudio();
    setBusy(true, "Agent 运行中…");
    state.streaming = true;
    state.live = emptyLive({
      userMessage: message,
      status: "开始处理…",
    });
    renderSession();
    try {
      const result = await streamEvents(
        `/api/sessions/${state.sessionId}/chat/stream`,
        { message },
        applyLiveEvent
      );
      if (result.turns?.length && result.detail_text) {
        result.turns[result.turns.length - 1]._detail_text = result.detail_text;
      }
      state.live = null;
      state.session = result;
      await refreshWorkspaces();
      renderSession();
      notifyRunFinished(result);
    } catch (err) {
      // 断线时服务端可能仍在等 ask：交给 pending_ask 轮询恢复
      const pending = state.live?.ask;
      if (pending) {
        state.live.status = "连接中断，请在右侧继续作答（或刷新页面）";
        renderSession();
        syncPendingAskFromSession({ pending_ask: {
          ask_id: pending.ask_id,
          question: pending.question,
          options: pending.options,
          questions: pending.questions,
        }});
      } else if (state.live) {
        state.live.error = err.message;
        state.live.status = "运行失败";
        renderSession();
        notifyRunFinished(null, { error: true });
        alert(err.message);
        state.live = null;
        renderSession();
      } else {
        notifyRunFinished(null, { error: true });
        alert(err.message);
      }
    } finally {
      state.streaming = false;
      if (!state.live?.ask) {
        setBusy(false);
        inputMessage.focus();
      }
    }
  };

  if (btnCancelRun) {
    btnCancelRun.onclick = async () => {
      if (!state.sessionId || !state.streaming) return;
      btnCancelRun.disabled = true;
      statusText.textContent = "正在停止…";
      try {
        await api(`/api/sessions/${state.sessionId}/cancel`, { method: "POST" });
        if (state.live) {
          state.live.status = "已请求取消，等待当前步结束…";
          updateLiveStatusDom();
        }
      } catch (err) {
        alert(err.message || String(err));
        btnCancelRun.disabled = false;
      }
    };
  }

  const btnSound = $("btn-sound");
  if (btnSound) {
    syncSoundButton();
    btnSound.onclick = () => {
      unlockAudio();
      const next = !isSoundMuted();
      setSoundMuted(next);
      // 开启时试听「待确认」提示（更接近飞书消息音）
      if (!next) playNotify("attention");
    };
  }
  document.addEventListener("pointerdown", () => unlockAudio(), { once: true });

  inputMessage.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      $("form-chat").requestSubmit();
    }
  });

  const btnOpenAsk = $("btn-open-ask");
  if (btnOpenAsk) {
    btnOpenAsk.onclick = () => openPendingAskPanel();
  }

  // 仅在「等待确认」时低频轮询；空闲不再每几秒打 GET（避免日志刷屏）
  async function pollPendingAskOnce() {
    if (!state.sessionId) return;
    if (state.streaming) return;
    if (state.live?.ask?.submitting) return;
    try {
      const s = await api(`/api/sessions/${state.sessionId}`);
      state.session = { ...state.session, ...s };
      if (s.pending_ask?.ask_id) {
        if (!state.live?.ask || state.live.ask.ask_id !== s.pending_ask.ask_id) {
          syncPendingAskFromSession(s);
          renderSession();
        } else {
          updateAskFallbackButton();
        }
      } else {
        updateAskFallbackButton();
      }
    } catch {
      /* ignore poll errors */
    }
  }

  setInterval(() => {
    if (state.live?.ask) void pollPendingAskOnce();
  }, 5000);

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void pollPendingAskOnce();
  });

  (async function init() {
    setBusy(true, "加载…");
    try {
      await refreshWorkspaces();
      if (state.workspaces.length) {
        state.workspaceId = state.workspaces[0].id;
        renderTree();
        const firstActive = sessionsOf(state.workspaces[0]).find((s) => !s.archived);
        if (firstActive) {
          await selectSession(firstActive.session_id, state.workspaceId);
        } else {
          renderSession();
        }
      } else {
        renderSession();
      }
    } catch (err) {
      statusText.textContent = err.message;
    } finally {
      if (!state.live?.ask) setBusy(false);
    }
  })();
})();
