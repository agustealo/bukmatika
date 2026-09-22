import { DossierClient } from "../../components/dossier-client";

type DossierPageProps = {
  searchParams: Promise<{
    provider?: string;
    record_id?: string;
    work_id?: string;
  }>;
};

export default async function DossierPage({ searchParams }: DossierPageProps) {
  const { provider, record_id: recordId, work_id: workId } = await searchParams;
  const hasSourceIdentity = Boolean(provider && recordId);

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
          <a href="/research">Research</a>
          <a href="/personalization">AI</a>
        </nav>
      </header>

      {workId ? <DossierClient workId={workId} /> : null}
      {!workId && hasSourceIdentity ? (
        <DossierClient provider={provider!} recordId={recordId!} />
      ) : null}
      {!workId && !hasSourceIdentity ? (
        <div className="error-card" role="alert">
          This dossier link is missing its canonical work or source identity.
        </div>
      ) : null}
    </main>
  );
}
