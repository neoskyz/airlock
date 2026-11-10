import { Component, type ReactNode } from 'react';

// A failed lazy import stays cached until reload. Fetch the latest application
// entry point when a deployment replaces the audit table's chunk.
export class AuditLogBoundary extends Component<{
  children: ReactNode;
}, {
  failed: boolean;
}> {
  state = {
    failed: false
  };
  static getDerivedStateFromError() {
    return {
      failed: true
    };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return <div className="empty-panel" role="alert">
    <h3>Audit table unavailable</h3>
    <p>The table could not be loaded. Reload to fetch the latest application files.</p>
    <button className="button secondary" onClick={() => window.location.reload()}>Reload</button>
  </div>;
  }
}
