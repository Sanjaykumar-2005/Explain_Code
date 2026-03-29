import type { AnalyzeResponse } from "./types";

export function getApiBaseUrl(): string {
  return (
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) ||
    "http://127.0.0.1:8000"
  );
}

/** Full URL for the analyze endpoint (shown in UI / browser console). */
export function getAnalyzeEndpointUrl(): string {
  return `${getApiBaseUrl().replace(/\/$/, "")}/api/analyze`;
}

async function readErrorMessage(res: Response): Promise<string> {
  try {
    const j = (await res.json()) as { detail?: unknown };
    const d = j.detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((x) => (typeof x === "object" && x && "msg" in x ? String((x as { msg: string }).msg) : String(x)))
        .join("; ");
    }
  } catch {
    /* ignore */
  }
  return res.statusText || `HTTP ${res.status}`;
}

/** Thrown when POST /api/analyze returns a non-2xx response (so the UI can show HTTP status). */
export class AnalyzeHttpError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AnalyzeHttpError";
    this.status = status;
  }
}

export async function analyzeCode(code: string, language: string | undefined): Promise<AnalyzeResponse> {
  const url = getAnalyzeEndpointUrl();
  if (process.env.NODE_ENV === "development") {
    console.info("[Explain Code] POST", url);
  }
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, language: language || undefined }),
  });
  if (process.env.NODE_ENV === "development") {
    console.info("[Explain Code] response", res.status, res.statusText, url);
  }
  if (!res.ok) {
    throw new AnalyzeHttpError(res.status, await readErrorMessage(res));
  }
  return res.json() as Promise<AnalyzeResponse>;
}
