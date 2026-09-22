import { DiscoveryClient } from "../components/discovery-client";

export default function Home() {
  return (
    <main className="app-shell">
      <header className="masthead">
        <a className="brand" href="/" aria-label="Bukmatika home">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span>Bukmatika</span>
        </a>
        <nav className="top-nav" aria-label="Primary navigation">
          <a href="/" aria-current="page">Discover</a>
          <a href="/library">Library</a>
        </nav>
      </header>

      <section className="hero">
        <div className="eyebrow">Find → verify → collect → understand</div>
        <h1>Ask for a body of knowledge, not a filename.</h1>
        <p>
          Search trusted book catalogs and the open web, resolve editions, verify access rights,
          and turn what you find into a research-ready library.
        </p>
      </section>

      <DiscoveryClient />

      <section className="principles" aria-label="Product principles">
        <article>
          <span>01</span>
          <h2>Rights before download</h2>
          <p>Discover broadly. Acquire only when evidence permits it.</p>
        </article>
        <article>
          <span>02</span>
          <h2>Edition intelligence</h2>
          <p>Works, editions, and actual files stay distinct instead of collapsing into one row.</p>
        </article>
        <article>
          <span>03</span>
          <h2>Provenance everywhere</h2>
          <p>Metadata, access state, and future research answers remain traceable to sources.</p>
        </article>
      </section>
    </main>
  );
}
