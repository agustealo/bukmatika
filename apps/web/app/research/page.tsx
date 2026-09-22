import { ResearchClient } from "../../components/research-client";

export default function ResearchPage() {
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
          <a href="/research" aria-current="page">Research</a>
        </nav>
      </header>

      <ResearchClient />
    </main>
  );
}
