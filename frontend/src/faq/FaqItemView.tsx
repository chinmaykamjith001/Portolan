import { useConcept } from "../api/hooks";
import { anchorLabel } from "../detail/anchors";
import { Chip } from "../design/primitives";
import { Markdown } from "../detail/markdown";
import type { FAQItem } from "../api/types";
import { useUI } from "../state/store";
import styles from "./faq.module.css";
import { safeUrl, sourceLabel } from "./faq";

/** One related concept. Concepts below the master layer aren't cached yet, so ask for the name
 * (a database read on the server, no LLM) instead of showing an anonymous link. */
function ConceptRow({ id, known }: { id: string; known: string | undefined }) {
  const paperId = useUI((s) => s.paperId);
  const focusConcept = useUI((s) => s.focusConcept);
  const { data } = useConcept(known === undefined ? paperId : null, id);
  const title = known ?? data?.concept.title;
  return (
    <li>
      {title && <span className={styles.conceptName}>{title}</span>}
      <button type="button" className={styles.link} onClick={() => focusConcept(id, "chart")}>
        Show on Chart
        {title && <span className={styles.hidden}> for {title}</span>}
      </button>
      <button type="button" className={styles.link} onClick={() => focusConcept(id, "route")}>
        Find on Route
        {title && <span className={styles.hidden}> for {title}</span>}
      </button>
    </li>
  );
}

export function FaqItemView({
  item,
  titleOf,
  fresh,
}: {
  item: FAQItem;
  titleOf: (conceptId: string) => string | undefined;
  /** Asked in this session: marked in words, not color. */
  fresh?: boolean;
}) {
  const url = safeUrl(item.source_url);
  const label = sourceLabel(item);
  const concepts = item.supported ? item.concept_ids : [];

  return (
    <article className={styles.item} aria-label={item.question}>
      {fresh && <p className={styles.fresh}>Just asked</p>}
      <h2 className={styles.question}>{item.question}</h2>
      {item.supported ? (
        <div className={styles.answer}>
          <Markdown source={item.answer_markdown} />
        </div>
      ) : (
        <p className={styles.unsupported}>
          Not supported by the sources. The paper, the code and the collected discussions don&apos;t answer this
          clearly.
        </p>
      )}
      <p className={styles.source}>
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer">
            {label}
          </a>
        ) : (
          label
        )}
      </p>
      {item.supported && item.anchors_cited.length > 0 && (
        <div className={styles.chips} aria-label="Cited in the paper">
          {item.anchors_cited.map((id) => (
            <Chip key={id} title={id}>
              {anchorLabel(id)}
            </Chip>
          ))}
        </div>
      )}
      {concepts.length > 0 && (
        <ul className={styles.concepts} aria-label="Related concepts">
          {concepts.map((id) => (
            <ConceptRow key={id} id={id} known={titleOf(id)} />
          ))}
        </ul>
      )}
    </article>
  );
}
