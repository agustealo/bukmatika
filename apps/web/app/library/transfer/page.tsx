import { LibraryPortabilityClient } from "../../../components/library-portability-client";

export default function LibraryTransferPage() {
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
          <a href="/library/transfer" aria-current="page">Backup</a>
          <a href="/status">Status</a>
          <a href="/research">Research</a>
          <a href="/personalization">AI</a>
        </nav>
      </header>

      <LibraryPortabilityClient />
    </main>
  );
}
