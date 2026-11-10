import type { Audit, Explainable } from './types';
import { ConsoleLink, policyParameterPath } from './consoleNavigation';
import { displayText } from './runUpdates';

export function DecisionLatency({ row }: { row: Explainable }) {
  const measured = row.latency?.decision_ms;
  const review = row.ai_review_detail;
  const source = review?.reviewer === 'AI' ? 'rules + AI review' : 'rules';
  const duration = measured == null ? '' : measured >= 1000 ? `${(measured / 1000).toFixed(1)} s` : `${Math.round(measured)} ms`;
  return <span className="latency-line">{measured == null ? 'Decision latency not recorded' : `Decision ${duration} · ${source}`}</span>;
}

export function AIReviewSummary({ row }: { row: Explainable }) {
  const review = row.ai_review_detail;
  if (review?.reviewer !== 'AI' || !review.summary) return null;
  return <p className="ai-review-summary">AI review: {displayText(review.summary).split(/\s+/).slice(0, 20).join(' ')}</p>;
}

export function DecisionMetrics({ stats }: { stats: Audit['stats'] }) {
  return <div className="decision-metrics" aria-label="Decision performance">
    <div title="p50 of payment events with recorded decision latency. Excludes approval, payment execution.">
      <span>Median decision latency</span>
      <strong>{stats.median_decision_latency_ms == null ? '—' : `${Math.round(stats.median_decision_latency_ms * 100) / 100} ms`}</strong>
      <small>p50 · recorded payment decisions</small>
    </div>
    <div title="Actual synchronous model attempts, including failed attempts, divided by payment decisions with recorded review metadata. Cached reviews and rules fallback do not count as model attempts.">
      <span>AI-reviewed decisions</span>
      <strong>{stats.ai_reviewed_decisions_pct == null ? '—' : `${Math.round(stats.ai_reviewed_decisions_pct * 10) / 10}%`}</strong>
      <small>Synchronous model calls · recorded decisions</small>
    </div>
  </div>;
}

export function VelocityParameterLinks({ code }: { code?: string }) {
  if (code !== 'velocity') return null;
  return <span className="factor-parameter-links"><ConsoleLink href={policyParameterPath('velocity_window_seconds')}>Window</ConsoleLink><ConsoleLink href={policyParameterPath('velocity_call_threshold')}>Call threshold</ConsoleLink></span>;
}
