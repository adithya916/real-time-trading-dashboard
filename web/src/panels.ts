import { lazy, type ComponentType, type LazyExoticComponent } from 'react';

// Every dashboard view registers itself here with one line, so branches that add views never
// edit the same layout code. The layout renders whatever is listed, in order.
export interface Panel {
  id: string;
  title: string;
  // Grid columns out of 12 at desktop width.
  span: 4 | 8 | 12;
  component: LazyExoticComponent<ComponentType>;
}

export const panels: Panel[] = [
  { id: 'stats', title: 'Metrics', span: 12, component: lazy(() => import('./panels/Stats')) },
  {
    id: 'chart',
    title: 'Mid, microprice and VWAP',
    span: 8,
    component: lazy(() => import('./panels/PriceChart')),
  },
  { id: 'book', title: 'Order book', span: 4, component: lazy(() => import('./panels/Book')) },
  {
    id: 'candles',
    title: 'One-second candles',
    span: 8,
    component: lazy(() => import('./panels/Candles')),
  },
  { id: 'trades', title: 'Trades', span: 4, component: lazy(() => import('./panels/Trades')) },
  {
    id: 'heatmap',
    title: 'Depth heatmap (±15 bps)',
    span: 12,
    component: lazy(() => import('./panels/Heatmap')),
  },
];
