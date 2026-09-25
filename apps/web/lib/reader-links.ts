export function readerPositionHref(sectionOrdinal: number, charOffset: number): string {
  const params = new URLSearchParams({
    section: String(sectionOrdinal),
    offset: String(charOffset),
  });
  return `?${params.toString()}`;
}

export function readerSourceHref(
  libraryEntryId: string,
  documentId: string,
  sectionId: string,
  sectionOrdinal: number,
  charOffset: number,
): string {
  const position = readerPositionHref(sectionOrdinal, charOffset);
  return `/read/${encodeURIComponent(libraryEntryId)}/${encodeURIComponent(documentId)}${position}#reader-section-${encodeURIComponent(sectionId)}`;
}
