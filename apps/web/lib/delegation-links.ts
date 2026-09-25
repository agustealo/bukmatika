export function delegationControlHref(delegationId: string): string {
  const encoded = encodeURIComponent(delegationId);
  return `/personalization?delegation=${encoded}#delegation-${encoded}`;
}
