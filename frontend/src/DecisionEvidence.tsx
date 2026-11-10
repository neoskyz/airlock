import type { Audit, Explainable } from './types';
import { displayText } from './runUpdates';

export function DecisionLatency({ row }: { row: Explainable }) {
  const measured = row.latency?.decision_ms;
  const review = row.ai_review_detail;
  const source = review?.reviewer === 'AI' ? `AI review${review.cached ? ' (cached)' : ''}` : 'rules';
  return <span className="latency-line">
    {measured == null ? 'Decision latency not recorded' : `Decision ${Math.round(measured)} ms · ${source}`}
    {measured != null && review?.reviewer === 'AI' && <span className="latency-note">rules {Math.round(row.latency?.rules_ms || 0)} ms · AI review {Math.round(row.latency?.ai_review_ms || 0)} ms</span>}
  </span>;
}

export function AIExplanationPanel({ row }: { row: Explainable & { decision?: string } }) {
  const explanation = row.ai_explanation;
  if (!explanation && !['BLOCK', 'REQUIRE_APPROVAL'].includes(row.computed_decision || row.decision || '')) return null;
  const text = !explanation ? 'AI explanation not recorded. Rule factors explain this decision.'
    : explanation.status === 'generating' ? 'AI explanation generating…'
    : explanation.status === 'completed' ? displayText(explanation.text)
    : explanation.status === 'skipped_budget' ? 'Daily LLM budget exhausted. Rule factors explain this decision.'
    : explanation.status === 'skipped_no_model' ? 'AI explanation unavailable · rules only.'
    : 'AI explanation unavailable. Rule factors explain this decision.';
  return <div className="ai-explanation" aria-live="polite">
    <span className="explanation-label">AI explanation (async)</span>
    <p role={explanation?.status === 'generating' ? 'status' : undefined}>{text}</p>
    {explanation?.status === 'completed' && explanation.latency_ms != null && <small>Explanation {Math.round(explanation.latency_ms)} ms · async · decision unchanged</small>}
  </div>;
}

export function DecisionMetrics({ stats }: { stats: Audit['stats'] }) {
  return <div className="decision-metrics" aria-label="Decision performance">
    <div title="p50 of payment events with recorded decision latency. Excludes approval, payment execution, and asynchronous explanation time.">
      <span>Median decision latency</span>
      <strong>{stats.median_decision_latency_ms == null ? '—' : `${Math.round(stats.median_decision_latency_ms * 100) / 100} ms`}</strong>
      <small>p50 · recorded payment decisions</small>
    </div>
    <div title="Actual synchronous model attempts, including failed attempts, divided by payment decisions with recorded review metadata. Cached reviews, rules fallback, and asynchronous explanations do not count as model attempts.">
      <span>AI-reviewed decisions</span>
      <strong>{stats.ai_reviewed_decisions_pct == null ? '—' : `${Math.round(stats.ai_reviewed_decisions_pct * 10) / 10}%`}</strong>
      <small>Synchronous model calls · recorded decisions</small>
    </div>
  </div>;
}
