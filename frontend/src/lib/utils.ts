import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** The shadcn class merger: conditional classes in, conflicting Tailwind utilities out. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
