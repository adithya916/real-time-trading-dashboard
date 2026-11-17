import { Suspense, useEffect } from 'react';
import { DEFAULT_SYMBOL, DEFAULT_VENUE, streamUrl } from './config';
import Header from './Header';
import { startMarket } from './market';
import { panels } from './panels';

const SPAN = { 4: 'lg:col-span-4', 8: 'lg:col-span-8', 12: 'lg:col-span-12' } as const;

export default function App() {
  useEffect(() => startMarket(streamUrl(DEFAULT_VENUE, DEFAULT_SYMBOL)), []);
  return (
    <div className="min-h-screen bg-slate-900 p-4 text-slate-100 md:p-6">
      <Header />
      <main className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {panels.map(({ id, title, span, component: Panel }) => (
          <section
            key={id}
            className={`rounded-xl border border-slate-700 bg-slate-800/60 p-3 ${SPAN[span]}`}
          >
            <h2 className="mb-2 text-sm font-semibold text-slate-300">{title}</h2>
            <Suspense fallback={<div className="h-24" />}>
              <Panel />
            </Suspense>
          </section>
        ))}
      </main>
    </div>
  );
}
