import type { ComponentType } from 'react';

// Every dashboard view registers itself here with one line, so branches that add views never
// edit the same layout code. The layout renders whatever is listed, in order.
export interface Panel {
  id: string;
  title: string;
  // Grid columns out of 12 at desktop width.
  span: number;
  component: ComponentType;
}

export const panels: Panel[] = [];
