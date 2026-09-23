import { LibraryStatusClient } from "../../components/library-status-client";
import styles from "./status.module.css";

export default function StatusPage() {
  return (
    <main className={`app-shell ${styles.statusRoot}`}>
      <header className="masthead">
        <a className="brand" href="/" aria-label="Bukmatika home">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span>Bukmatika</span>
        </a>
        <nav className="top-nav" aria-label="Primary navigation">
          <a href="/">Discover</a>
          <a href="/library">Library</a>
          <a href="/status" aria-current="page">Status</a>
          <a href="/research">Research</a>
          <a href="/personalization">AI</a>
        </nav>
      </header>

      <LibraryStatusClient />
    </main>
  );
}
