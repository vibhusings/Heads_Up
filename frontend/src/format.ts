// Display-only helpers. No arrival math or status derivation lives here.

export function timeIn(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZone, timeZoneName: "short" }).format(
    new Date(iso),
  );
}

export function dateTimeIn(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
    timeZoneName: "short",
  }).format(new Date(iso));
}

export function minutes(seconds: number): string {
  return `${Math.round(seconds / 60)} min`;
}

export function label(value: string): string {
  return value.replace(/_/g, " ");
}
