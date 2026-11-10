export type AgentMode = 'live' | 'compromised';
export type Decision = 'ALLOW' | 'BLOCK' | 'REQUIRE_APPROVAL' | 'BYPASS';
export interface Config {
  demo_mode: boolean;
  agent_mode: AgentMode;
  live_available: boolean;
  llm_degraded: boolean;
  judge_mode?: string;
  paypal_mode: string;
  agent_model: string;
  judge_model: string;
  policy_yaml: string;
}
export interface Product {
  id: string;
  name: string;
  price: string | number;
  currency: string;
  merchant: string;
  description: string;
}
export interface TimelineEvent {
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
  source?: string;
  paypal_id?: string;
  paypal_status?: string;
  status?: string;
  mock?: boolean;
}
export interface Approval {
  id: string;
  tool: string;
  amount: number | string;
  payee: string;
  reason: string;
  status: string;
}
export interface Run {
  id: string;
  status: 'running' | 'awaiting_approval' | 'completed' | 'failed';
  firewall_enabled: boolean;
  agent_mode: AgentMode;
  messages: { role: string; content: string }[];
  events: TimelineEvent[];
  approvals: Approval[];
  error?: string;
  judge_mode?: string;
  llm_degraded?: boolean;
}
export interface AuditRow {
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
}
export interface Audit {
  rows: AuditRow[];
  stats: { blocked_amount: number | string; allowed_amount: number | string };
}
