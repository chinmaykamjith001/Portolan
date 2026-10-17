import { Logo } from "../design/Logo";
import styles from "./App.module.css";

const EXAMPLES = [
  { label: "Attention Is All You Need", input: "https://arxiv.org/abs/1706.03762" },
  { label: "LoRA: Low-Rank Adaptation of Large Language Models", input: "https://arxiv.org/abs/2106.09685" },
];

// M0 shell: empty state only. Paper ingestion and the views arrive in later milestones.
export function App() {
  return (
    <>
      <header className={styles.topbar}>
        <span className={styles.brand}>
          <Logo />
          Portolan
        </span>
        <input
          className={styles.input}
          aria-label="Paper"
          placeholder="Paste an arXiv link, DOI, PDF or title"
        />
      </header>
      <main className={styles.empty}>
        <p>Paste a paper to map its ideas and find where each one lives in the code.</p>
        <ul>
          {EXAMPLES.map((e) => (
            <li key={e.input}>
              <a href={e.input} onClick={(ev) => ev.preventDefault()}>
                {e.label}
              </a>
            </li>
          ))}
        </ul>
      </main>
    </>
  );
}
