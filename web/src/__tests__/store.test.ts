import { describe, expect, it, vi } from 'vitest';
import { createStore } from '../store';

describe('createStore', () => {
  it('returns the initial state and applies updates', () => {
    const store = createStore({ count: 0 });
    store.set({ count: 1 });
    store.set((previous) => ({ count: previous.count + 1 }));
    expect(store.get()).toEqual({ count: 2 });
  });

  it('notifies subscribers on change and stops after unsubscribe', () => {
    const store = createStore(0);
    const listener = vi.fn();
    const unsubscribe = store.subscribe(listener);
    store.set(1);
    unsubscribe();
    store.set(2);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it('skips listeners when the value did not change', () => {
    const store = createStore('a');
    const listener = vi.fn();
    store.subscribe(listener);
    store.set('a');
    expect(listener).not.toHaveBeenCalled();
  });
});
