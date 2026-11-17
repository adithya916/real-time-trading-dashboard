export const fmt = (value: number | null | undefined, digits = 2) =>
  value == null || Number.isNaN(value)
    ? '---'
    : value.toLocaleString('en-US', {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
