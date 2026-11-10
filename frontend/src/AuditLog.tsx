import { useMemo, useState } from 'react';
import { AllCommunityModule, ModuleRegistry, themeQuartz, type ColDef, type ICellRendererParams } from 'ag-grid-community';
import { AgGridReact } from 'ag-grid-react';
import { ConsoleLink, eventMoney as money, eventPath, navigateConsole } from './consoleNavigation';
import { EventDecision } from './EventDetail';
import { Icon } from './ui';
import { displayText as neutral } from './runUpdates';
import type { AuditRow } from './types';
ModuleRegistry.registerModules([AllCommunityModule]);
const time = (value: string) => new Date(value).toLocaleTimeString('en-US', {
  hour12: false
});
const gridTheme = themeQuartz.withParams({
  backgroundColor: '#121821',
  foregroundColor: '#dce3ed',
  headerBackgroundColor: '#19212d',
  headerTextColor: '#8f9db1',
  borderColor: '#293344',
  accentColor: '#8b9aff',
  fontFamily: 'Inter, ui-sans-serif, system-ui, sans-serif',
  fontSize: 15,
  rowHoverColor: '#ffffff05',
  wrapperBorderRadius: 10
});
export default function AuditLog({
  rows
}: {
  rows: AuditRow[];
}) {
  const [filter, setFilter] = useState('');
  const columns = useMemo<ColDef<AuditRow>[]>(() => [{
    field: 'id',
    headerName: 'Event',
    minWidth: 150,
    cellRenderer: (params: ICellRendererParams<AuditRow>) => params.data ? <ConsoleLink className="event-link" href={eventPath(params.data.id)} aria-label={`View audit event ${params.data.id}`} onClick={event => event.stopPropagation()}>View details →</ConsoleLink> : null
  }, {
    field: 'timestamp',
    headerName: 'Time',
    minWidth: 115,
    valueFormatter: params => time(params.value)
  }, {
    field: 'tool',
    headerName: 'Tool / event',
    minWidth: 175
  }, {
    field: 'amount',
    headerName: 'Amount',
    minWidth: 115,
    cellDataType: 'number',
    valueGetter: params => Number(params.data?.amount || 0),
    valueFormatter: params => money(params.value, params.data?.currency)
  }, {
    field: 'payee',
    headerName: 'Payee',
    minWidth: 230
  }, {
    field: 'decision',
    headerName: 'Decision',
    minWidth: 210,
    cellRenderer: (params: ICellRendererParams<AuditRow>) => params.data ? <EventDecision row={params.data} /> : null
  }, {
    field: 'risk_score',
    headerName: 'Risk',
    minWidth: 85
  }, {
    field: 'mode',
    headerName: 'Mode',
    minWidth: 110
  }, {
    field: 'status',
    headerName: 'Execution',
    minWidth: 140
  }, {
    field: 'paypal_id',
    headerName: 'PayPal ID',
    minWidth: 210
  }, {
    field: 'paypal_status',
    headerName: 'PayPal status',
    minWidth: 160
  }, {
    field: 'reason',
    headerName: 'Reason',
    minWidth: 420,
    valueFormatter: params => neutral(params.value)
  }], []);
  return <>
    <div className="audit-toolbar">
      <label>
        <Icon name="search" size={18} />
        <input placeholder="Search tool, payee, ID, reason…" value={filter} onChange={event => setFilter(event.target.value)} aria-label="Search audit log" />
      </label>
      <span>{rows.length} events · AG Grid Community</span>
    </div>
    <div className="audit-grid">
      <AgGridReact<AuditRow> theme={gridTheme} rowData={rows} columnDefs={columns} defaultColDef={{
        sortable: true,
        filter: true,
        resizable: true,
        flex: 1
      }} quickFilterText={filter} rowHeight={55} headerHeight={48} getRowId={params => params.data.id} onRowClicked={event => {
        if (event.data) navigateConsole(eventPath(event.data.id));
      }} rowClassRules={{
        'audit-risk': params => params.data?.decision === 'BLOCK'
      }} overlayNoRowsTemplate={filter ? "<span>No audit events match your search.</span>" : "<span>No audit events yet. Start a task in the client.</span>"} />
    </div>
    <p className="audit-caption">SQLite audit ledger · Decisions reflect the active mode at evaluation. PayPal acceptance does not confirm settlement.</p>
  </>;
}
