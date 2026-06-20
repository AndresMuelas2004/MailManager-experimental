import { es } from './es';
import { en } from './en';
import type { Dictionary } from '../translate';
import type { Lang } from '../types';

// The ``as Dictionary`` cast widens the const-typed locales to the loose
// nested-string shape ``translate`` consumes. Both locales share ``es``'s
// shape (``en`` is typed ``EsDictionary``), so the cast is safe.
export const dictionaries: Record<Lang, Dictionary> = {
  es: es as unknown as Dictionary,
  en: en as unknown as Dictionary,
};

// Spanish is the fallback when a key is missing from the active locale —
// the app default and the most complete dictionary.
export const FALLBACK_DICTIONARY: Dictionary = dictionaries.es;
