export type BugSeverity = "critical" | "high" | "medium" | "low" | "info" | string;

export type BugItem = {
  severity: BugSeverity;
  title: string;
  description: string;
  suggestion: string | null;
  line_hint: number | null;
};

export type AnalyzeResponse = {
  summary: string;
  logic_explanation: string;
  bugs: BugItem[];
  time_complexity: string;
  space_complexity: string;
  complexity_notes: string;
  raw_model_error?: string | null;
};
