/** SelfAgent SSE / REST 事件与会话类型（对齐 web store）。 */

export type ProgressEvent =
  | { type: "status"; phase?: string; message?: string; step?: number }
  | { type: "assistant_delta"; delta?: string; step?: number; phase?: string }
  | { type: "skill"; skill?: string; status?: string; message?: string; ok?: boolean }
  | { type: "step"; index?: number; thought?: string; todos?: unknown; [k: string]: unknown }
  | {
      type: "ask_user";
      ask_id: string;
      question?: string;
      options?: string[];
      questions?: unknown[];
    }
  | { type: "cancelled"; message?: string }
  | { type: "done"; session?: SessionDetail }
  | { type: "error"; message?: string }
  | { type: string; [k: string]: unknown };

export interface Workspace {
  id: string;
  path: string;
  title: string;
  sessions?: SessionSummary[];
  session_count?: number;
}

export interface SessionSummary {
  session_id: string;
  title?: string;
  preview?: string;
  starred?: boolean;
  archived?: boolean;
  workspace_id?: string;
}

export interface SessionDetail extends SessionSummary {
  workdir?: string;
  mode?: string;
  turns?: Turn[];
  pending_plan?: Record<string, unknown> | null;
  pending_ask?: Record<string, unknown> | null;
  todos?: unknown;
  answer?: string;
  completed?: boolean;
  detail_text?: string;
}

export interface Turn {
  index?: number;
  user?: string;
  answer?: string;
  completed?: boolean;
  changes?: Record<string, unknown>[];
  ask_answers?: unknown[];
}
