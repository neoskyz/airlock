export type AgentMode = 'live' | 'compromised';
export type AgentProvider = 'openai' | 'anthropic';
export type Decision = 'ALLOW' | 'BLOCK' | 'REQUIRE_APPROVAL' | 'BYPASS';
export type Mode = 'Monitor' | 'Enforce';
export interface Config {
  mode: Mode;
  version?: string;
  deployed_on_render?: boolean;
  demo_mode: boolean;
  agent_mode: AgentMode;
  live_available: boolean;
  llm_degraded: boolean;
  judge_mode?: string;
  paypal_mode: string;
  agent_provider?: AgentProvider;
  agent_model?: string;
  judge_model?: string;
  policy_yaml: string;
}
export interface Product {
  id: string;
  name: string;
  title?: string;
  price: string | number;
  currency: string;
  merchant: string;
  domain?: string;
  description: string;
  image?: string;
  image_url?: string;
  url?: string;
  category?: string;
}
export interface RiskFactor { name: string; points: number; reason: string }
export interface Latency { rules_ms: number; ai_review_ms: number; total_ms?: number }
export interface Explainable {
  risk_score?: number;
  factors?: RiskFactor[];
  latency?: Latency;
  ai_review?: string;
  policy?: string;
  mode?: Mode;
  enforced_decision?: Decision;
  computed_decision?: Decision;
  display_decision?: string;
}
export interface TimelineEvent extends Explainable {
  id: string;
  type: string;
  message: string;
  decision?: Decision;
  tool?: string;
  amount?: number | string;
  payee?: string;
  reason?: string;
  suspicious_excerpt?: string;
  products?: Product[];
  product?: Product;
  source?: string;
  paypal_id?: string;
  paypal_status?: string;
  status?: string;
  mock?: boolean;
}
export interface Approval extends Explainable {
  user_request?: string;
  id: string;
  tool: string;
  amount: number | string;
  payee: string;
  reason: string;
  status: string;
  provenance?: { id: string; name: string; origin: string; text: string }[];
  suspicious_excerpt?: string;
  source?: string;
}
export interface Run {
  id: string;
  status: 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled';
  firewall_enabled?: boolean;
  agent_mode?: AgentMode;
  agent_provider?: AgentProvider | 'scripted';
  agent_model?: string;
  judge_model?: string;
  messages: { role: string; content: string }[];
  events: TimelineEvent[];
  approvals: Approval[];
  error?: string;
  judge_mode?: string;
  llm_degraded?: boolean;
}
export interface AuditRow extends Explainable {
  id: string;
  timestamp: string;
  run_id: string;
  tool: string;
  amount: number | string;
  currency: string;
  payee: string;
  decision: Decision;
  reason: string;
  paypal_id?: string;
  paypal_status?: string;
  status: string;
  mock: boolean;
  firewall_enabled: boolean;
  suspicious_excerpt?: string;
  source?: string;
  provenance?: { id: string; name: string; origin: string; text: string }[];
}
export interface Audit {
  rows: AuditRow[];
  stats: { blocked_amount: number | string; allowed_amount: number | string };
}
export interface Wallet { balance: string | number; currency: string; label: string; source: string; paypal_mode?: string }
export interface LiveState { config: Config; runs: Run[]; audit: Audit; wallet: Wallet }
export interface Policy {
  currency: string;
  single_transaction_limit: string | number;
  daily_limit: string | number;
  period: 'daily' | 'weekly';
  auto_approve_threshold: string | number;
  allowed_payees: string[];
  unknown_payee_action: string;
  blocked_categories: string[];
  risk_approval_threshold: number;
  risk_block_threshold: number;
  enabled: Record<string, boolean>;
  [key: string]: unknown;
}
