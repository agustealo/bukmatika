export function readerPositionHref(sectionOrdinal: number, charOffset: number): string {
  const params = new URLSearchParams({
    section: String(sectionOrdinal),
    offset: String(charOffset),
  });
  return `?${params.toString()}`;
}
