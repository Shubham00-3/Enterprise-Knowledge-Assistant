import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowUp,
  FileText,
  Loader2,
  LogOut,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  Upload,
  X,
} from "lucide-react";

import { Login } from "./Login";
import { authEnabled, supabase } from "./supabaseClient";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const EXAMPLES = [
  "What is the employee paid leave policy?",
  "What should API clients do for 429 responses?",
  "How often are high-risk vendors reviewed?",
  "When are terminated employee accounts disabled?",
];

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function App() {
  const [session, setSession] = useState(null);
  const [authReady, setAuthReady] = useState(!authEnabled);
  const [documents, setDocuments] = useState([]);
  const [entries, setEntries] = useState([]);
  const [question, setQuestion] = useState("");
  const [conversationId, setConversationId] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [upload, setUpload] = useState(null); // { name, status }
  const [artifact, setArtifact] = useState({
    open: false,
    loading: false,
    data: null,
    error: "",
    highlightChunkId: null,
    highlightChunkIds: [],
  });
  const threadEndRef = useRef(null);
  const fileRef = useRef(null);

  const token = session?.access_token ?? null;

  // Track the Supabase session when auth is enabled.
  useEffect(() => {
    if (!authEnabled) return;
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => sub.subscription.unsubscribe();
  }, []);

  function authHeaders(json = true) {
    const headers = {};
    if (json) headers["Content-Type"] = "application/json";
    if (token) headers["Authorization"] = `Bearer ${token}`;
    return headers;
  }

  async function fetchDocuments() {
    const res = await fetch(`${API_BASE}/documents`, { headers: authHeaders(false) });
    return res.ok ? res.json() : [];
  }

  // Load the signed-in user's documents (or the seed pool when auth is off).
  useEffect(() => {
    if (authEnabled && !session) {
      setDocuments([]);
      return;
    }
    fetchDocuments().then(setDocuments).catch(() => setDocuments([]));
  }, [session]);

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
        headers: authHeaders(),
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

  async function onUploadFile(event) {
    const file = event.target.files?.[0];
    event.target.value = ""; // allow re-uploading the same file name later
    if (!file) return;
    setError("");
    setUpload({ name: file.name, status: "uploading" });
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(`${API_BASE}/upload`, {
        method: "POST",
        headers: authHeaders(false), // no Content-Type: browser sets the multipart boundary
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || "Upload failed.");
      }
      const data = await res.json();
      setUpload({ name: file.name, status: "processing" });
      await pollUntilIndexed(data.document_id, file.name);
    } catch (err) {
      setUpload({ name: file.name, status: "failed", error: err.message });
    }
  }

  async function pollUntilIndexed(documentId, name) {
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await sleep(2500);
      const docs = await fetchDocuments();
      const doc = docs.find((d) => d.id === documentId);
      if (doc && doc.status === "indexed") {
        setDocuments(docs);
        setUpload({ name, status: "indexed" });
        return;
      }
      if (doc && doc.status === "failed") {
        setUpload({ name, status: "failed", error: "Indexing failed on the server." });
        return;
      }
    }
    setUpload({ name, status: "processing", error: "Still indexing — check back shortly." });
  }

  async function openArtifact(source) {
    if (!source?.document_id) return;
    const highlightChunkIds = source.chunk_ids || (source.chunk_id ? [source.chunk_id] : []);
    setArtifact({
      open: true,
      loading: true,
      data: null,
      error: "",
      highlightChunkId: source.chunk_id,
      highlightChunkIds,
    });
    try {
      const res = await fetch(`${API_BASE}/documents/${source.document_id}/artifact`, {
        headers: authHeaders(false),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || "Could not open the document.");
      }
      const data = await res.json();
      setArtifact({
        open: true,
        loading: false,
        data,
        error: "",
        highlightChunkId: source.chunk_id,
        highlightChunkIds,
      });
    } catch (err) {
      setArtifact({
        open: true,
        loading: false,
        data: null,
        error: err.message,
        highlightChunkId: source.chunk_id,
        highlightChunkIds,
      });
    }
  }

  function closeArtifact() {
    setArtifact({
      open: false,
      loading: false,
      data: null,
      error: "",
      highlightChunkId: null,
      highlightChunkIds: [],
    });
  }

  async function signOut() {
    await supabase.auth.signOut();
    setEntries([]);
    setConversationId(null);
    setDocuments([]);
    closeArtifact();
  }

  if (authEnabled && !authReady) {
    return (
      <div className="auth-screen">
        <Loader2 className="spin" size={22} />
      </div>
    );
  }
  if (authEnabled && !session) {
    return <Login />;
  }

  const hasThread = entries.length > 0;

  return (
    <div className="page">
      <header className="topbar">
        <div className="wordmark">
          <Sparkles size={18} />
          <span>Knowledge Assistant</span>
        </div>
        <div className="topbar-actions">
          {indexedCount > 0 && <span className="indexed-pill">{indexedCount} documents indexed</span>}
          <button className="icon-btn" onClick={() => fileRef.current?.click()}>
            <Upload size={15} /> Upload
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.txt,.docx"
            onChange={onUploadFile}
            hidden
          />
          {authEnabled && session && (
            <>
              <span className="user-chip" title={session.user?.email}>
                {session.user?.email}
              </span>
              <button className="icon-btn" onClick={signOut} aria-label="Sign out">
                <LogOut size={15} />
              </button>
            </>
          )}
        </div>
      </header>

      {upload && <UploadBanner upload={upload} onDismiss={() => setUpload(null)} />}

      {!hasThread ? (
        <main className="hero">
          <h1>Ask your company&apos;s knowledge base</h1>
          <p className="hero-sub">Grounded answers with citations from your internal documents.</p>
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
              onOpenArtifact={openArtifact}
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
      <DocumentArtifactPanel artifact={artifact} onClose={closeArtifact} />
    </div>
  );

  async function sendFeedback(index, messageId, rating) {
    await fetch(`${API_BASE}/feedback`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ message_id: messageId, rating }),
    }).catch(() => null);
    setEntries((items) =>
      items.map((item, i) =>
        i === index ? { ...item, answer: { ...item.answer, feedback: rating } } : item
      )
    );
  }
}

function UploadBanner({ upload, onDismiss }) {
  const labels = {
    uploading: "Uploading",
    processing: "Indexing",
    indexed: "Indexed",
    failed: "Upload failed",
  };
  const busy = upload.status === "uploading" || upload.status === "processing";
  return (
    <div className={`upload-banner ${upload.status}`}>
      {busy ? <Loader2 className="spin" size={14} /> : <FileText size={14} />}
      <span>
        <strong>{labels[upload.status]}:</strong> {upload.name}
        {upload.error ? ` — ${upload.error}` : ""}
      </span>
      {!busy && (
        <button className="banner-dismiss" onClick={onDismiss} aria-label="Dismiss">
          ✕
        </button>
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

function Entry({ entry, index, loading, onFeedback, onOpenArtifact }) {
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
          <div className="answer-block">
            <div className="section-label">Answer</div>
            {answer.status === "insufficient_context" && (
              <div className="abstain-note">Not found in the knowledge base</div>
            )}
            <p className="answer-text">{answer.answer}</p>
          </div>

          {answer.sources?.length > 0 && (
            <CitationStrip sources={answer.sources} onOpenArtifact={onOpenArtifact} />
          )}

          <div className="answer-footer">
            <AnswerMetrics answer={answer} />
            <span className="dot">·</span>
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

function CitationStrip({ sources, onOpenArtifact }) {
  const citations = groupSources(sources);
  return (
    <div className="citations-block" aria-label="Citations">
      <div className="section-label">Citations</div>
      <div className="citation-row">
        {citations.map((citation) => (
          <button
            key={citation.document_id || citation.document}
            className="citation-chip"
            onClick={() =>
              onOpenArtifact({ ...citation.primary, chunk_ids: citation.chunk_ids })
            }
            title={`${citation.document}${citation.primary.page ? `, page ${citation.primary.page}` : ""}`}
          >
            <FileText size={13} />
            <span>{citation.document}</span>
            {citation.count > 1 && <em>{citation.count}</em>}
          </button>
        ))}
      </div>
    </div>
  );
}

function AnswerMetrics({ answer }) {
  const metrics = answer.metrics || {};
  const confidence = metrics.confidence ?? answer.confidence ?? 0;
  const citationCount = metrics.citation_count ?? answer.sources?.length ?? 0;
  const topSourceScore = metrics.top_source_score;
  const status = metrics.status ?? answer.status;
  const latency = metrics.latency_ms ?? answer.latency_ms;

  return (
    <div className="metric-row" aria-label="Answer metrics">
      <span className="metric-chip">
        {Math.round(confidence * 100)}% confidence
      </span>
      <span className="metric-chip">{citationCount} citations</span>
      <span className="metric-chip">
        top score {typeof topSourceScore === "number" ? topSourceScore.toFixed(2) : "n/a"}
      </span>
      <span className={`metric-chip ${status}`}>{statusLabel(status)}</span>
      <span className="metric-chip">{latency} ms</span>
    </div>
  );
}

function DocumentArtifactPanel({ artifact, onClose }) {
  const activeRef = useRef(null);

  useEffect(() => {
    if (!artifact.open || artifact.loading || !activeRef.current) return;
    activeRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [artifact.open, artifact.loading, artifact.highlightChunkId, artifact.data]);

  if (!artifact.open) return null;

  const document = artifact.data;

  return (
    <aside className="artifact-panel" aria-label="Document artifact">
      <div className="artifact-header">
        <div>
          <div className="section-label">Artifact</div>
          <h3>{document?.title || document?.document || "Document"}</h3>
          {document && (
            <p>
              {document.doc_type.toUpperCase()} / {document.chunks.length} indexed sections
            </p>
          )}
        </div>
        <button className="artifact-close" onClick={onClose} aria-label="Close artifact">
          <X size={18} />
        </button>
      </div>

      <div className="artifact-body">
        {artifact.loading && (
          <div className="artifact-state">
            <Loader2 className="spin" size={16} /> Loading document...
          </div>
        )}
        {artifact.error && <div className="artifact-error">{artifact.error}</div>}
        {!artifact.loading &&
          !artifact.error &&
          document?.chunks.map((chunk) => {
            const active = artifact.highlightChunkIds?.includes(chunk.chunk_id);
            const scrollTarget = chunk.chunk_id === artifact.highlightChunkId;
            return (
              <section
                key={chunk.chunk_id}
                ref={scrollTarget ? activeRef : null}
                className={`artifact-chunk ${active ? "highlight" : ""}`}
              >
                <div className="artifact-meta">
                  <span>{chunk.section_title || `Section ${chunk.chunk_index + 1}`}</span>
                  <span>p.{chunk.page}</span>
                </div>
                <p>{chunk.content}</p>
              </section>
            );
          })}
      </div>
    </aside>
  );
}

function groupSources(sources) {
  const grouped = new Map();
  for (const source of sources || []) {
    const key = source.document_id || source.document;
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, {
        document_id: source.document_id,
        document: source.document,
        count: 1,
        primary: source,
        chunk_ids: source.chunk_id ? [source.chunk_id] : [],
      });
      continue;
    }
    existing.count += 1;
    if (source.chunk_id && !existing.chunk_ids.includes(source.chunk_id)) {
      existing.chunk_ids.push(source.chunk_id);
    }
    if ((source.score ?? 0) > (existing.primary.score ?? 0)) {
      existing.primary = source;
    }
  }
  return [...grouped.values()];
}

function statusLabel(status) {
  if (status === "insufficient_context") return "needs more context";
  return "answered";
}
