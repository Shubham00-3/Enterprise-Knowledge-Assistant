import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  FileText,
  Loader2,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";
const AUTH_TOKEN = import.meta.env.VITE_API_AUTH_TOKEN || "";
const jsonHeaders = {
  "Content-Type": "application/json",
  ...(AUTH_TOKEN ? { Authorization: `Bearer ${AUTH_TOKEN}` } : {})
};

const EXAMPLES = [
  "What is the employee paid leave policy?",
  "What should API clients do for 429 responses?",
  "How often are high-risk vendors reviewed?",
  "When are terminated employee accounts disabled?",
];

export function App() {
  const [documents, setDocuments] = useState([]);
  const [entries, setEntries] = useState([]);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const threadEndRef = useRef(null);

  useEffect(() => {
    fetch(`${API_BASE}/documents`)
      .then((res) => (res.ok ? res.json() : []))
      .then(setDocuments)
      .catch(() => setDocuments([]));
  }, []);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries, loading]);

  const indexedCount = useMemo(
    () => documents.filter((doc) => doc.status === "indexed").length,
    [documents]
  );

  async function submit(value) {
    const text = (value ?? question).trim();
    if (!text || loading) return;
    setError("");
    setQuestion("");
    setLoading(true);
    setEntries((items) => [...items, { question: text, answer: null }]);
    try {
      const response = await fetch(`${API_BASE}/ask`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ question: text, conversation_id: conversationId }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail.detail || "The assistant could not answer right now.");
      }
      const answer = await response.json();
      setConversationId(answer.conversation_id);
      setEntries((items) =>
        items.map((item, i) => (i === items.length - 1 ? { ...item, answer } : item))
      );
    } catch (err) {
      setError(err.message);
      setEntries((items) => items.slice(0, -1));
    } finally {
      setLoading(false);
    }
  }

  async function sendFeedback(index, messageId, rating) {
    await fetch(`${API_BASE}/feedback`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ message_id: messageId, rating }),
    }).catch(() => null);
    setEntries((items) =>
      items.map((item, i) =>
        i === index ? { ...item, answer: { ...item.answer, feedback: rating } } : item
      )
    );
  }

  const hasThread = entries.length > 0;

  return (
    <div className="page">
      <header className="topbar">
        <div className="wordmark">
          <Sparkles size={18} />
          <span>Knowledge Assistant</span>
        </div>
        {indexedCount > 0 && <span className="indexed-pill">{indexedCount} documents indexed</span>}
      </header>

      {!hasThread ? (
        <main className="hero">
          <h1>Ask your company&apos;s knowledge base</h1>
          <p className="hero-sub">
            Grounded answers with citations from your internal documents.
          </p>
          <Composer
            value={question}
            onChange={setQuestion}
            onSubmit={() => submit()}
            loading={loading}
            autoFocus
          />
          <div className="examples">
            {EXAMPLES.map((example) => (
              <button key={example} className="example-chip" onClick={() => submit(example)}>
                {example}
              </button>
            ))}
          </div>
          {error && <p className="error">{error}</p>}
        </main>
      ) : (
        <main className="thread">
          {entries.map((entry, index) => (
            <Entry
              key={index}
              entry={entry}
              index={index}
              loading={loading && index === entries.length - 1 && !entry.answer}
              onFeedback={sendFeedback}
            />
          ))}
          {error && <p className="error">{error}</p>}
          <div ref={threadEndRef} />
        </main>
      )}

      {hasThread && (
        <div className="composer-dock">
          <Composer
            value={question}
            onChange={setQuestion}
            onSubmit={() => submit()}
            loading={loading}
            placeholder="Ask a follow-up..."
          />
        </div>
      )}
    </div>
  );
}

function Composer({ value, onChange, onSubmit, loading, placeholder, autoFocus }) {
  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <input
        autoFocus={autoFocus}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder || "Ask anything about your company's documents..."}
        maxLength={1200}
      />
      <button type="submit" disabled={loading || value.trim().length < 2} aria-label="Ask">
        {loading ? <Loader2 className="spin" size={18} /> : <ArrowUp size={18} />}
      </button>
    </form>
  );
}

function Entry({ entry, index, loading, onFeedback }) {
  const { question, answer } = entry;
  return (
    <section className="entry">
      <h2 className="question">{question}</h2>

      {loading && (
        <div className="searching">
          <Loader2 className="spin" size={16} /> Searching documents and composing a grounded answer...
        </div>
      )}

      {answer && (
        <>
          {answer.sources?.length > 0 && (
            <div className="sources-block">
              <div className="section-label">Sources</div>
              <div className="source-grid">
                {answer.sources.map((source, i) => (
                  <SourceCard key={`${source.document}-${i}`} index={i + 1} source={source} />
                ))}
              </div>
            </div>
          )}

          <div className="answer-block">
            <div className="section-label">Answer</div>
            {answer.status === "insufficient_context" && (
              <div className="abstain-note">Not found in the knowledge base</div>
            )}
            <p className="answer-text">{answer.answer}</p>
          </div>

          <div className="answer-footer">
            <span className={`confidence ${answer.status}`}>
              {Math.round((answer.confidence || 0) * 100)}% confidence
            </span>
            <span className="dot">·</span>
            <span className="latency">{answer.latency_ms} ms</span>
            <div className="spacer" />
            <button
              className={`feedback-btn ${answer.feedback === "up" ? "active" : ""}`}
              onClick={() => onFeedback(index, answer.message_id, "up")}
              aria-label="Helpful"
            >
              <ThumbsUp size={15} />
            </button>
            <button
              className={`feedback-btn ${answer.feedback === "down" ? "active" : ""}`}
              onClick={() => onFeedback(index, answer.message_id, "down")}
              aria-label="Not helpful"
            >
              <ThumbsDown size={15} />
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function SourceCard({ index, source }) {
  const [open, setOpen] = useState(false);
  return (
    <button className={`source-card ${open ? "open" : ""}`} onClick={() => setOpen((v) => !v)}>
      <div className="source-head">
        <span className="source-index">{index}</span>
        <FileText size={13} />
        <span className="source-name" title={source.document}>
          {source.document}
        </span>
        <span className="source-page">p.{source.page}</span>
      </div>
      <p className="source-snippet">{source.snippet}</p>
    </button>
  );
}
