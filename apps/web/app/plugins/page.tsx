import { WordPressConnectionManager } from "../../components/wordpress-connection-manager";

export default function PluginsPage() {
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
          <a href="/status">Status</a>
          <a href="/research">Research</a>
          <a href="/personalization">AI</a>
          <a href="/plugins" aria-current="page">Plugins</a>
        </nav>
      </header>
      <WordPressConnectionManager />
    </main>
  );
}
