"use client";

import dynamic from "next/dynamic";
import {
  AlertTriangle,
  Brain,
  Bug,
  ChevronRight,
  Clock,
  Code2,
  Loader2,
  Sparkles,
  Zap,
} from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { AnalyzeHttpError, analyzeCode, getAnalyzeEndpointUrl } from "@/lib/api";
import type { AnalyzeResponse, BugItem } from "@/lib/types";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[480px] min-h-[320px] items-center justify-center rounded-xl border border-white/10 bg-[#0d1117]">
      <Loader2 className="h-8 w-8 animate-spin text-cyan-400/80" aria-hidden />
    </div>
  ),
});

const LANGUAGES: { id: string; label: string; monaco: string }[] = [
  { id: "python", label: "Python", monaco: "python" },
  { id: "typescript", label: "TypeScript", monaco: "typescript" },
  { id: "javascript", label: "JavaScript", monaco: "javascript" },
  { id: "tsx", label: "TSX", monaco: "typescript" },
  { id: "jsx", label: "JSX", monaco: "javascript" },
  { id: "go", label: "Go", monaco: "go" },
  { id: "rust", label: "Rust", monaco: "rust" },
  { id: "java", label: "Java", monaco: "java" },
  { id: "cpp", label: "C++", monaco: "cpp" },
  { id: "c", label: "C", monaco: "c" },
  { id: "csharp", label: "C#", monaco: "csharp" },
  { id: "kotlin", label: "Kotlin", monaco: "kotlin" },
  { id: "swift", label: "Swift", monaco: "swift" },
  { id: "ruby", label: "Ruby", monaco: "ruby" },
  { id: "php", label: "PHP", monaco: "php" },
  { id: "sql", label: "SQL", monaco: "sql" },
  { id: "plaintext", label: "Plain text", monaco: "plaintext" },
];

const SAMPLE_PYTHON = `def two_sum(nums: list[int], target: int) -> list[int]:
    """Return indices i, j such that nums[i] + nums[j] == target."""
    seen: dict[int, int] = {}
    for i, n in enumerate(nums):
        need = target - n
        if need in seen:
            return [seen[need], i]
        seen[n] = i
    return []
`;

type TabId = "summary" | "logic" | "bugs" | "complexity";

const severityStyle = (s: string): string => {
  const x = s.toLowerCase();
  if (x === "critical") return "bg-rose-500/20 text-rose-200 ring-rose-500/40";
  if (x === "high") return "bg-orange-500/20 text-orange-200 ring-orange-500/40";
  if (x === "medium") return "bg-amber-500/15 text-amber-100 ring-amber-500/35";
  if (x === "low") return "bg-sky-500/15 text-sky-100 ring-sky-500/35";
  return "bg-zinc-500/20 text-zinc-200 ring-zinc-500/40";
};

function BugCard({ b }: { b: BugItem }) {
  return (
    <article
      className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-4 backdrop-blur-sm transition hover:border-cyan-500/20"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="font-medium text-zinc-100">{b.title || "Issue"}</h4>
        <span
          className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${severityStyle(b.severity)}`}
        >
          {b.severity}
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-zinc-400">{b.description}</p>
      {b.suggestion ? (
        <p className="mt-3 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2 text-sm text-emerald-100/90">
          <span className="font-medium text-emerald-300/90">Suggestion: </span>
          {b.suggestion}
        </p>
      ) : null}
      {b.line_hint != null ? (
        <p className="mt-2 font-mono text-xs text-zinc-500">Around line {b.line_hint}</p>
      ) : null}
    </article>
  );
}

export function CodeStudio() {
  const [lang, setLang] = useState(LANGUAGES[0]);
  const [code, setCode] = useState(SAMPLE_PYTHON);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [tab, setTab] = useState<TabId>("summary");
  const [apiHitLine, setApiHitLine] = useState<string | null>(null);

  const tabs = useMemo(
    () =>
      [
        { id: "summary" as const, label: "Summary", icon: Sparkles },
        { id: "logic" as const, label: "Logic", icon: Brain },
        { id: "bugs" as const, label: "Issues", icon: Bug },
        { id: "complexity" as const, label: "Complexity", icon: Clock },
      ] as const,
    []
  );

  const runAnalyze = useCallback(async () => {
    setError(null);
    setLoading(true);
    setResult(null);
    const url = getAnalyzeEndpointUrl();
    const t0 = performance.now();
    setApiHitLine(`POST ${url} …`);
    try {
      const data = await analyzeCode(code, lang.id);
      const ms = Math.round(performance.now() - t0);
      setApiHitLine(`POST ${url} · ${ms} ms · 200 OK`);
      setResult(data);
      if (data.raw_model_error) {
        setError(`Model output could not be parsed completely. ${data.raw_model_error}`);
      }
      if (data.bugs?.length) setTab("bugs");
      else setTab("summary");
    } catch (e) {
      const ms = Math.round(performance.now() - t0);
      if (e instanceof AnalyzeHttpError) {
        setApiHitLine(`POST ${url} · ${ms} ms · HTTP ${e.status}`);
        setError(e.message);
      } else {
        const msg = e instanceof Error ? e.message : "Request failed";
        setApiHitLine(`POST ${url} · ${ms} ms · failed (network or CORS)`);
        setError(msg);
      }
    } finally {
      setLoading(false);
    }
  }, [code, lang.id]);

  return (
    <div className="relative min-h-screen overflow-hidden font-sans text-zinc-100">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_120%_80%_at_50%_-20%,rgba(34,211,238,0.15),transparent),radial-gradient(ellipse_80%_50%_at_100%_50%,rgba(139,92,246,0.12),transparent)]" />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_bottom,transparent,rgba(9,9,11,0.85))]" />

      <div className="relative z-10 mx-auto flex min-h-screen max-w-[1600px] flex-col px-4 pb-16 pt-8 sm:px-6 lg:px-10">
        <header className="mb-8 flex flex-col gap-6 border-b border-white/[0.06] pb-8 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-cyan-400/90">
              <Code2 className="h-6 w-6" aria-hidden />
              <span className="text-sm font-medium uppercase tracking-widest">Explain Code</span>
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              AI code intelligence
            </h1>
            <p className="mt-2 max-w-xl text-base text-zinc-400">
              Logic walkthrough, bug hints, and complexity — powered by Gemini on a FastAPI backend.
            </p>
            <p className="mt-3 max-w-2xl break-all font-mono text-[11px] leading-relaxed text-zinc-500">
              <span className="text-zinc-600">API: </span>
              {getAnalyzeEndpointUrl()}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex flex-col gap-1.5 text-xs font-medium uppercase tracking-wide text-zinc-500">
              Language
              <select
                value={lang.id}
                onChange={(e) => {
                  const n = LANGUAGES.find((l) => l.id === e.target.value);
                  if (n) setLang(n);
                }}
                className="rounded-lg border border-white/10 bg-zinc-900/80 px-3 py-2 text-sm font-normal normal-case text-zinc-200 outline-none ring-cyan-500/0 transition focus:border-cyan-500/40 focus:ring-2 focus:ring-cyan-500/30"
              >
                {LANGUAGES.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.label}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={runAnalyze}
              disabled={loading || !code.trim()}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-cyan-500 to-violet-600 px-6 py-2.5 text-sm font-semibold text-white shadow-lg shadow-cyan-500/25 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <Zap className="h-4 w-4" aria-hidden />
              )}
              Analyze
            </button>
            {apiHitLine ? (
              <p className="w-full max-w-md break-all font-mono text-[11px] text-cyan-500/90 lg:max-w-xs lg:text-right">
                {apiHitLine}
              </p>
            ) : null}
          </div>
        </header>

        <div className="grid flex-1 gap-6 lg:grid-cols-[1fr_minmax(0,420px)] xl:grid-cols-[1fr_minmax(0,480px)]">
          <section className="flex min-h-0 flex-col gap-3">
            <div className="flex items-center justify-between text-xs text-zinc-500">
              <span className="flex items-center gap-1">
                <ChevronRight className="h-3 w-3" aria-hidden />
                Source
              </span>
              <span className="font-mono">{code.length.toLocaleString()} chars</span>
            </div>
            <div className="overflow-hidden rounded-xl border border-white/10 shadow-2xl shadow-black/40 ring-1 ring-white/[0.04]">
              <MonacoEditor
                height={480}
                language={lang.monaco}
                theme="vs-dark"
                value={code}
                onChange={(v) => setCode(v ?? "")}
                options={{
                  minimap: { enabled: true },
                  fontSize: 14,
                  fontFamily: "var(--font-geist-mono), ui-monospace, monospace",
                  scrollBeyondLastLine: false,
                  padding: { top: 16, bottom: 16 },
                  wordWrap: "on",
                  tabSize: 4,
                }}
              />
            </div>
          </section>

          <aside className="flex min-h-[320px] flex-col gap-3 lg:min-h-0">
            <div className="flex flex-wrap gap-1 rounded-xl border border-white/10 bg-zinc-950/50 p-1 backdrop-blur-md">
              {tabs.map(({ id, label, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  className={`flex flex-1 items-center justify-center gap-1.5 rounded-lg px-3 py-2 text-xs font-medium transition sm:text-sm ${
                    tab === id
                      ? "bg-white/10 text-white shadow-sm"
                      : "text-zinc-500 hover:bg-white/5 hover:text-zinc-300"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5 opacity-80" aria-hidden />
                  {label}
                  {id === "bugs" && result?.bugs?.length ? (
                    <span className="rounded-full bg-rose-500/30 px-1.5 py-0.5 text-[10px] text-rose-100">
                      {result.bugs.length}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>

            <div className="flex min-h-[280px] flex-1 flex-col rounded-xl border border-white/10 bg-zinc-950/60 p-5 shadow-inner backdrop-blur-md lg:min-h-[480px]">
              {error ? (
                <div className="mb-4 flex gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" aria-hidden />
                  <p>{error}</p>
                </div>
              ) : null}

              {!result && !loading && !error ? (
                <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center text-zinc-500">
                  <Sparkles className="h-10 w-10 opacity-40" aria-hidden />
                  <p className="max-w-xs text-sm">
                    Paste or edit code, then run <strong className="text-zinc-400">Analyze</strong> to see
                    explanations and complexity.
                  </p>
                </div>
              ) : null}

              {loading ? (
                <div className="flex flex-1 flex-col items-center justify-center gap-4 py-12">
                  <Loader2 className="h-10 w-10 animate-spin text-cyan-400/70" aria-hidden />
                  <p className="text-sm text-zinc-400">Calling Gemini…</p>
                </div>
              ) : null}

              {result && !loading ? (
                <div className="prose prose-invert prose-sm max-w-none flex-1 overflow-y-auto pr-1">
                  {tab === "summary" ? (
                    <p className="leading-relaxed text-zinc-300">{result.summary || "—"}</p>
                  ) : null}
                  {tab === "logic" ? (
                    <div className="whitespace-pre-wrap leading-relaxed text-zinc-300">
                      {result.logic_explanation || "—"}
                    </div>
                  ) : null}
                  {tab === "bugs" ? (
                    <div className="flex flex-col gap-3">
                      {result.bugs?.length ? (
                        result.bugs.map((b, i) => <BugCard key={`${b.title}-${i}`} b={b} />)
                      ) : (
                        <p className="text-zinc-500">No issues reported. (Still verify manually.)</p>
                      )}
                    </div>
                  ) : null}
                  {tab === "complexity" ? (
                    <div className="space-y-4">
                      <div>
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                          Time
                        </h3>
                        <p className="mt-1 font-mono text-lg text-cyan-200/90">
                          {result.time_complexity || "—"}
                        </p>
                      </div>
                      <div>
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                          Space
                        </h3>
                        <p className="mt-1 font-mono text-lg text-violet-200/90">
                          {result.space_complexity || "—"}
                        </p>
                      </div>
                      <div>
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                          Notes
                        </h3>
                        <p className="mt-1 leading-relaxed text-zinc-400">
                          {result.complexity_notes || "—"}
                        </p>
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </aside>
        </div>
      </div>
    </div>
  );
}
