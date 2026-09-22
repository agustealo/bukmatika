import { DossierClient } from "../../components/dossier-client";

type DossierPageProps = {
  searchParams: Promise<{
    provider?: string;
    record_id?: string;
  }>;
};

export default async function DossierPage({ searchParams }: DossierPageProps) {
  const { provider, record_id: recordId } = await searchParams;

  return (
    <main className="app-shell">
      <header className="masthead">
        <a className="brand" href="/" aria-label="Bukmatika home">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span>Bukmatika</span>
        </a>
        <nav className="top-nav" aria-label="Primary navigation">
          <a href="/">Discover</a>
          <a href="/library">Library</a>
        </nav>
      </header>

      {provider && recordId ? (
        <DossierClient provider={provider} recordId={recordId} />
      ) : (
        <div className="error-card" role="alert">
          This dossier link is missing its source identity.
        </div>
      )}
    </main>
  );
}
