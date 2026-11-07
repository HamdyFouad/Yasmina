import type { UnitOfMeasure } from '@/types/domain'

/** Business-friendly labels for the German GAEB unit enums. */
const UNIT_LABELS: Record<UnitOfMeasure, string> = {
  STUECK: 'Stück',
  PACK: 'Pack',
  KARTON: 'Karton',
  PALETTE: 'Palette',
  ROLLE: 'Rolle',
  KILOGRAMM: 'kg',
  LITER: 'l',
  METER: 'm',
  QUADRATMETER: 'm²',
  NOT_APPLICABLE: '—',
}

export function unitLabel(unit: UnitOfMeasure): string {
  return UNIT_LABELS[unit]
}
