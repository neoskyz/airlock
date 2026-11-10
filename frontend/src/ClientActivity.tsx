import { useEffect, useRef, useState } from 'react';
import { displayText, isRunActive } from './runUpdates';
import type { Product, Run, TimelineEvent } from './types';
import { eventMoney as money } from './consoleNavigation';
import './clientActivity.css';
export function productHref(product: Product): string | undefined {
  if (product.url === '/store/volt-65w') return product.url;
  try {
    const url = new URL(product.url || '');
    return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
const productName = (product: Product) => product.title || product.name;
type Step = {
  id: string;
  kind: 'search' | 'page' | 'payment';
  event: TimelineEvent;
  products?: Product[];
  selectedProduct?: string;
  result?: TimelineEvent;
  pending?: boolean;
  product?: Product;
};

// Associate results with the operation that emitted them, retaining each search batch.
export function activitySteps(events: TimelineEvent[]): Step[] {
  const steps: Step[] = [];
  let catalog: Step | undefined;
  let payment: Step | undefined;
  for (const event of events) {
    if (event.type === 'searching') {
      catalog = {
        id: event.id,
        kind: 'search',
        event
      };
      steps.push(catalog);
    } else if (event.type === 'products' || event.products) {
      if (!catalog || catalog.products !== undefined || catalog.result) {
        catalog = {
          id: event.id,
          kind: 'search',
          event
        };
        steps.push(catalog);
      }
      catalog.products = event.products || [];
    } else if (event.type === 'catalog_error') {
      if (catalog) catalog.result = event;
    } else if (event.type === 'product_page' && event.product) {
      if (catalog?.products?.some(product => product.id === event.product?.id)) catalog.selectedProduct = event.product.id;
      steps.push({
        id: event.id,
        kind: 'page',
        event,
        product: event.product
      });
    } else if (event.type === 'checkout') {
      payment = {
        id: event.id,
        kind: 'payment',
        event,
        product: event.tool === 'create_order' ? [...steps].reverse().find(step => step.kind === 'page')?.product : undefined
      };
      steps.push(payment);
    } else if (event.status === 'awaiting_approval') {
      if (payment && !payment.result) payment.pending = true;
    } else if (event.type === 'tool_result') {
      if (!payment || payment.result) {
        payment = {
          id: event.id,
          kind: 'payment',
          event
        };
        steps.push(payment);
      }
      payment.result = event;
      payment.pending = false;
    }
  }
  return steps;
}
export function ProductImage({
  product,
  large = false
}: {
  product: Product;
  large?: boolean;
}) {
  const [failedUrl, setFailedUrl] = useState('');
  const audio = /headphone|earbud|audio/i.test(`${productName(product)} ${product.category || ''}`);
  const url = product.image || product.image_url;
  return <div className={`product-image ${large ? 'large' : ''}`}>
    {url && failedUrl !== url ? <img src={url} alt={productName(product)} onError={() => setFailedUrl(url)} loading="lazy" /> : <div className={`product-placeholder ${audio ? 'audio' : 'charging'}`} role="img" aria-label={audio ? 'Headphones image unavailable' : 'Product image unavailable'}>
      {audio ? <svg width="76" height="76" viewBox="0 0 80 80" fill="none" stroke="currentColor" strokeWidth="5" aria-hidden="true">
        <path d="M14 46V34a26 26 0 0 1 52 0v12" />
        <rect x="10" y="39" width="15" height="28" rx="6" />
        <rect x="55" y="39" width="15" height="28" rx="6" />
      </svg> : <div className="charger-illustration">
        <span />
        <i />
        <b />
      </div>}
    </div>}
  </div>;
}
function StepHeader({
  number,
  title,
  description,
  state,
  label
}: {
  number: number;
  title: string;
  description?: string;
  state: string;
  label: string;
}) {
  return <div className="trace-step activity-step-header">
    <span className={`step-icon activity-step-icon ${state}`} aria-hidden="true">
      {state === 'complete' ? '✓' : number}
    </span>
    <div className="trace-text">
      <h3>
        {title}
      </h3>
      {description && <p>
        {displayText(description)}
      </p>}
    </div>
    <span className={`step-status activity-step-status ${state}`}>
      {state === 'active' && <i />}
      {label}
    </span>
  </div>;
}
function Storefront({
  product
}: {
  product: Product;
}) {
  return <div className="storefront">
    <div className="browser-bar">
      <i />
      <i />
      <i />
      <span>
        {product.url || product.domain || product.merchant}
      </span>
    </div>
    <div className="storefront-content">
      <ProductImage product={product} large />
      <div>
        <span className="storefront-brand">
          {product.domain || product.merchant}
        </span>
        <h3>
          {productName(product)}
        </h3>
        <p>
          {product.description}
        </p>
        <strong>
          {money(product.price, product.currency)}
        </strong>
        <span className="storefront-stock"><i />Available for checkout</span>
      </div>
    </div>
  </div>;
}
function PaymentStep({
  step,
  number,
  active,
  interrupted
}: {
  step: Step;
  number: number;
  active: boolean;
  interrupted: boolean;
}) {
  const event = step.result || step.event;
  const rejected = event.status === 'blocked' || event.status === 'denied' || event.decision === 'BLOCK';
  const accepted = Boolean(step.result?.paypal_id);
  const unknown = event.status === 'unknown';
  const waiting = Boolean(active && step.pending && !step.result);
  const unresolved = !active && !step.result;
  const state = accepted ? 'complete' : rejected || unknown || interrupted || unresolved ? 'interrupted' : active && !step.result ? 'active' : 'complete';
  const title = accepted ? step.event.tool === 'create_order' ? 'PayPal order created' : 'PayPal payout submitted' : rejected ? 'Payment tool rejected' : unknown ? 'Payment outcome unknown' : waiting ? 'Awaiting payment approval' : unresolved ? 'Payment result unavailable' : 'Checkout';
  const description = accepted ? step.product ? productName(step.product) : event.message : rejected ? 'The payment tool rejected this request. No PayPal call was made.' : unknown ? 'Verify the PayPal sandbox before retrying this payment.' : interrupted || unresolved ? 'The task ended before a payment result was received. Check the payment status before retrying.' : waiting ? 'The agent will continue when the payment request receives a response.' : step.product ? `Preparing checkout for ${productName(step.product)}` : 'Submitting payment request';
  return <article className={`activity-operation payment-operation ${state}`}>
    <StepHeader number={number} title={title} description={description} state={state} label={accepted ? 'Accepted' : rejected ? 'Rejected' : unknown || unresolved ? 'Unconfirmed' : waiting ? 'Waiting' : interrupted ? 'Interrupted' : active && !step.result ? 'In progress' : step.result ? 'Complete' : 'No result'} />
    <div className="activity-payment-body">
      <span className="trace-amount">
        {money(step.event.amount ?? event.amount)}
        {step.event.payee || event.payee ? ` → ${step.event.payee || event.payee}` : ''}
      </span>
      {accepted && <div className="payment-receipt">
        <code>
          {event.paypal_id}
        </code>
        <span className="badge">
          {event.paypal_status || 'ACCEPTED'}
        </span>
        {event.mock && <span className="badge amber">MOCK</span>}
      </div>}
    </div>
  </article>;
}
export function ClientActivity({
  run
}: {
  run: Run;
}) {
  const steps = activitySteps(run.events || []);
  const active = isRunActive(run.status);
  const end = useRef<HTMLDivElement>(null);
  const last = steps.at(-1);
  let current = 'Planning the next step';
  if (run.status === 'awaiting_approval') current = 'Waiting for payment approval';else if (last?.kind === 'search') current = last.products !== undefined ? `Comparing ${last.products.length} catalog results` : 'Searching the product catalog';else if (last?.kind === 'page' && last.product) current = `Reading product page · ${productName(last.product)}`;else if (last?.kind === 'payment' && !last.result) current = last.product ? `Checking out · ${productName(last.product)}` : `Submitting ${last.event.tool || 'payment request'}`;
  useEffect(() => {
    end.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest'
    });
  }, [run.id, run.events.length, run.status]);
  return <div className="activity-trace client-activity">
    {active && <div className="current-activity" role="status" aria-live="polite">
      <i />
      <div>
        <span>Current step</span>
        <strong>
          {current}
        </strong>
      </div>
    </div>}
    <ol className="activity-step-list">
      {steps.map((step, index) => {
        const isLast = index === steps.length - 1;
        const interrupted = isLast && !active && ['failed', 'cancelled'].includes(run.status) && !step.result;
        if (step.kind === 'payment') return <li key={step.id}>
        <PaymentStep step={step} number={index + 1} active={active && isLast} interrupted={interrupted} />
      </li>;
        if (step.kind === 'page' && step.product) {
          const state = interrupted ? 'interrupted' : 'complete';
          return <li key={step.id}>
        <article className={`activity-operation page-operation ${state}`}>
          <StepHeader number={index + 1} title="Opened product page" description={productName(step.product)} state={state} label={interrupted ? 'Interrupted' : 'Complete'} />
          <Storefront product={step.product} />
        </article>
      </li>;
        }
        const ready = step.products !== undefined;
        const failed = step.result?.type === 'catalog_error';
        const state = failed ? 'interrupted' : ready ? 'complete' : active && isLast ? 'active' : 'interrupted';
        return <li key={step.id}>
        <article className={`activity-operation search-operation ${state}`}>
          <StepHeader number={index + 1} title="Catalog search" description={failed ? step.result?.message : ready ? `${step.products?.length} products found for your request` : 'Finding products that match your request'} state={state} label={failed ? 'Unavailable' : ready ? 'Complete' : active && isLast ? 'In progress' : 'Interrupted'} />
          {ready && <>
            <div className="product-grid">
              {step.products?.map(product => <a key={product.id} href={productHref(product)} target="_blank" rel="noopener noreferrer" className={`product-card ${step.selectedProduct === product.id ? 'agent-selected' : step.selectedProduct ? 'agent-alternative' : ''}`} aria-label={`${productName(product)}${step.selectedProduct === product.id ? ', selected by agent' : ''}`}>
                <div className="activity-product-image">
                  <ProductImage product={product} />
                  {step.selectedProduct === product.id && <span className="agent-selection-badge">✓ Selected by agent</span>}
                </div>
                <div className="product-card-body">
                  <span className="merchant-domain">
                    {product.domain || product.merchant}
                  </span>
                  <h4>
                    {productName(product)}
                  </h4>
                  <div className="product-bottom">
                    <strong>
                      {money(product.price, product.currency)}
                    </strong>
                    <span>
                      {product.category || 'Electronics'}
                    </span>
                  </div>
                </div>
              </a>)}
            </div>
            <div className="catalog-credit">Product catalog by <strong>Channel3</strong></div>
          </>}
        </article>
      </li>;
      })}
    </ol>
    {run.status === 'completed' && <div className="task-finished">✓ Task finished<span>Orders and payouts retain their PayPal status; acceptance does not confirm settlement.</span></div>}
    {run.status === 'cancelled' && <div className="task-finished">Task stopped. Submitted requests are not rolled back.</div>}
    {run.error && <div className="error-banner" role="alert">The agent could not complete this task. Check the payment status before retrying.</div>}
    <div ref={end} />
  </div>;
}
