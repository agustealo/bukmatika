import { LibraryClient } from "../../components/library-client";

export default function LibraryPage() {
  return (
    <main className="app-shell">
      <header className="masthead">
        <a className="brand" href="/" aria-label="Bukmatika home">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span>Bukmatika</span>
        </a>
        <nav className="top-nav" aria-label="Primary navigation">
          <a href="/">Discover</a>
          <a href="/library" aria-current="page">Library</a>
        </nav>
      </header>

      <LibraryClient />
    </main>
  );
}
