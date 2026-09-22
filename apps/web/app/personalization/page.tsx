import { LocalAIStatus } from "../../components/local-ai-status";
import { PersonalizationClient } from "../../components/personalization-client";
import { PersonalizationDataControls } from "../../components/personalization-data-controls";

export default function PersonalizationPage() {
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
          <a href="/personalization" aria-current="page">AI</a>
        </nav>
      </header>

      <PersonalizationClient />
      <LocalAIStatus />
      <PersonalizationDataControls />
    </main>
  );
}
