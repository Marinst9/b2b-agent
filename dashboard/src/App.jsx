import { useState, useEffect, useRef } from "react";

const API_BASE = "http://127.0.0.1:8000";

function StatusPill({ status }) {
  const map = {
    new: { bg: "#F1F5F9", color: "#475569", label: "Нов" },
    drafted: { bg: "#FEF9C3", color: "#854D0E", label: "Испратен предлог" },
    approved: { bg: "#DBEAFE", color: "#1E40AF", label: "Одобрено" },
    rejected: { bg: "#FEE2E2", color: "#991B1B", label: "Одбиено" },
    sent: { bg: "#DCFCE7", color: "#166534", label: "Испратено" },
    dry_run_sent: { bg: "#E0E7FF", color: "#3730A3", label: "Симулирано (dry-run)" },
    send_failed: { bg: "#FEE2E2", color: "#991B1B", label: "Неуспешно" },
  };
  const s = map[status] || map.new;
  return (
    <span style={{ background: s.bg, color: s.color, padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 600, whiteSpace: "nowrap" }}>
      {s.label}
    </span>
  );
}

function ApprovalPill({ draft }) {
  if (!draft) return <span style={{ fontSize: "12px", color: "#94A3B8" }}>Нема предлог</span>;
  if (draft.is_approved_for_send) {
    return <span style={{ fontSize: "11px", fontWeight: 700, color: "#166534" }}>✅ Одобрено (v{draft.approved_version})</span>;
  }
  if (draft.approval_status === "approved") {
    return <span style={{ fontSize: "11px", fontWeight: 700, color: "#B45309" }}>⚠ Одобрувањето е поништено (изменето по одобрување)</span>;
  }
  if (draft.approval_status === "rejected") {
    return <span style={{ fontSize: "11px", fontWeight: 700, color: "#991B1B" }}>Одбиено</span>;
  }
  return <span style={{ fontSize: "11px", fontWeight: 700, color: "#64748B" }}>На чекање</span>;
}

const REVIEW_FLAG_COLORS = {
  exclusion_triggered: "#991B1B",
  invalid_fact_reference: "#B45309",
  unsupported_inference: "#B45309",
  missing_research: "#92400E",
  missing_qualification: "#92400E",
  stale_qualification: "#92400E",
  expired_research: "#92400E",
  conflicting_research_evidence: "#92400E",
  generic_draft: "#92400E",
  manually_edited: "#64748B",
};

function ReviewFlags({ flags }) {
  if (!flags || flags.length === 0) return null;
  return (
    <div style={{ marginBottom: "8px" }}>
      {flags.map((f, i) => (
        <div key={i} style={{ fontSize: "11px", color: REVIEW_FLAG_COLORS[f.type] || "#64748B", marginBottom: "3px" }}>
          ⚠ {f.detail}
        </div>
      ))}
    </div>
  );
}

function ClaimSources({ claims }) {
  if (!claims || claims.length === 0) return null;
  return (
    <div style={{ marginBottom: "8px" }}>
      <p style={{ fontSize: "11px", fontWeight: 700, color: "#166534", textTransform: "uppercase", margin: "0 0 4px" }}>
        Тврдења поткрепени со докази ({claims.length})
      </p>
      <ul style={{ margin: 0, paddingLeft: "18px" }}>
        {claims.map((c, i) => (
          <li key={i} style={{ fontSize: "12px", color: "#334155", marginBottom: "4px" }}>
            {c.claim} —{" "}
            <a href={c.source_url} target="_blank" rel="noreferrer" style={{ color: "#2563EB" }}>
              извор
            </a>
            <div style={{ fontSize: "11px", color: "#94A3B8" }}>„{c.excerpt}"</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function DraftHistory({ draftId }) {
  const [history, setHistory] = useState(null);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (!open && !history) {
      const res = await fetch(`${API_BASE}/drafts/${draftId}/history`);
      setHistory(await res.json());
    }
    setOpen(!open);
  };

  return (
    <div style={{ marginTop: "8px" }}>
      <button onClick={toggle} style={{ background: "none", border: "none", color: "#2563EB", fontSize: "11px", cursor: "pointer", padding: 0 }}>
        {open ? "▲ Сокриј историја на верзии" : "▼ Прикажи историја на верзии"}
      </button>
      {open && history && (
        <div style={{ marginTop: "6px", borderLeft: "2px solid #E2E8F0", paddingLeft: "10px" }}>
          {history.map((v) => (
            <div key={v.id} style={{ marginBottom: "8px" }}>
              <p style={{ fontSize: "11px", fontWeight: 700, color: "#64748B", margin: 0 }}>
                v{v.version_number} {v.edited_manually ? "(рачно уредено)" : `(генерирано · ${v.model})`} · {new Date(v.created_at).toLocaleString()}
              </p>
              <p style={{ fontSize: "12px", color: "#1E293B", margin: "2px 0", fontWeight: 600 }}>{v.subject}</p>
              <p style={{ fontSize: "12px", color: "#64748B", margin: 0, whiteSpace: "pre-wrap" }}>{v.body}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Keyed by `${lead.id}-${draft.version}` at the call site so that whenever the
// draft's version changes (edit, regenerate, or a fresh generate), React
// mounts a brand-new instance of this component with fresh local state
// initialized straight from the new props -- no effect needed to keep
// subject/body "in sync" with a prop that can change out from under them.
function DraftCard({ lead, campaignId, onChanged }) {
  const draft = lead.draft;
  const [subject, setSubject] = useState(draft?.subject || "");
  const [body, setBody] = useState(draft?.body || "");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [language, setLanguage] = useState(draft?.language || "en");
  const [tone, setTone] = useState(draft?.tone || "professional");
  const [length, setLength] = useState(draft?.length || "medium");
  const [error, setError] = useState(null);

  const call = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      const res = await fn();
      if (res && !res.ok) {
        const body = await res.json();
        setError(body.detail || "Failed.");
        return;
      }
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const saveEdit = () =>
    call(() =>
      fetch(`${API_BASE}/drafts/${draft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, body }),
      }).then((res) => {
        if (res.ok) setDirty(false);
        return res;
      })
    );

  const approve = () => call(() => fetch(`${API_BASE}/drafts/${draft.id}/approve`, { method: "POST" }));
  const reject = () => call(() => fetch(`${API_BASE}/drafts/${draft.id}/reject`, { method: "POST" }));
  const regenerate = () =>
    call(() =>
      fetch(`${API_BASE}/campaigns/${campaignId}/drafts/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lead_ids: [lead.id], language, tone, length }),
      })
    );

  return (
    <div style={{ background: "white", borderRadius: "12px", padding: "1.2rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.6rem" }}>
        <div>
          <p style={{ margin: 0, fontWeight: 700, fontSize: "14px", color: "#1E3A5F" }}>{lead.name} — {lead.company}</p>
          <p style={{ margin: "2px 0 0", fontSize: "12px", color: "#64748B" }}>
            {lead.email || "нема email адреса"} {draft && `· верзија ${draft.version}`}
          </p>
        </div>
        <ApprovalPill draft={draft} />
      </div>

      <div style={{ display: "flex", gap: "8px", marginBottom: "10px", flexWrap: "wrap" }}>
        <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ padding: "5px 8px", borderRadius: "6px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="en">English</option>
          <option value="mk">Македонски</option>
        </select>
        <select value={tone} onChange={(e) => setTone(e.target.value)} style={{ padding: "5px 8px", borderRadius: "6px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="professional">professional</option>
          <option value="friendly">friendly</option>
          <option value="formal">formal</option>
          <option value="casual">casual</option>
        </select>
        <select value={length} onChange={(e) => setLength(e.target.value)} style={{ padding: "5px 8px", borderRadius: "6px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="short">short</option>
          <option value="medium">medium</option>
          <option value="long">long</option>
        </select>
        <button disabled={busy} onClick={regenerate} style={btnStyle(!busy, "#7C3AED")}>
          ✨ {draft ? "Регенерирај" : "Генерирај"}
        </button>
      </div>

      {error && <p style={{ fontSize: "12px", color: "#991B1B", marginBottom: "8px" }}>❌ {error}</p>}

      {!draft ? (
        <p style={{ fontSize: "12px", color: "#94A3B8" }}>Сè уште нема генериран предлог за овој лид.</p>
      ) : (
        <>
          <ReviewFlags flags={draft.review_flags} />
          <input
            value={subject}
            onChange={(e) => { setSubject(e.target.value); setDirty(true); }}
            style={{ width: "100%", boxSizing: "border-box", padding: "8px 10px", fontSize: "13px", fontWeight: 600, border: "1.5px solid #E2E8F0", borderRadius: "8px", marginBottom: "8px" }}
          />
          <textarea
            value={body}
            onChange={(e) => { setBody(e.target.value); setDirty(true); }}
            rows={4}
            style={{ width: "100%", boxSizing: "border-box", padding: "8px 10px", fontSize: "13px", border: "1.5px solid #E2E8F0", borderRadius: "8px", resize: "vertical", fontFamily: "inherit" }}
          />
          <ClaimSources claims={draft.claim_sources} />
          <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
            <button disabled={!dirty || busy} onClick={saveEdit} style={btnStyle(dirty && !busy, "#0F766E")}>
              Зачувај измена
            </button>
            <button disabled={busy || !lead.email} onClick={approve} style={btnStyle(!busy && lead.email, "#2563EB")}>
              Одобри
            </button>
            <button disabled={busy} onClick={reject} style={btnStyle(!busy, "#DC2626")}>
              Одбиј
            </button>
          </div>
          {!lead.email && <p style={{ fontSize: "11px", color: "#B45309", marginTop: "6px" }}>Не може да се одобри — лидот нема email адреса.</p>}
          <DraftHistory draftId={draft.id} />
        </>
      )}
    </div>
  );
}

function btnStyle(enabled, color) {
  return {
    background: enabled ? color : "#CBD5E1",
    color: "white",
    border: "none",
    borderRadius: "8px",
    padding: "7px 14px",
    fontSize: "12px",
    fontWeight: 600,
    cursor: enabled ? "pointer" : "not-allowed",
  };
}

function ResearchStatusPill({ status }) {
  const map = {
    needs_website: { bg: "#FEF3C7", color: "#92400E", label: "Потребен website" },
    pending: { bg: "#F1F5F9", color: "#475569", label: "Не е стартувано" },
    in_progress: { bg: "#DBEAFE", color: "#1E40AF", label: "Се обработува..." },
    completed: { bg: "#DCFCE7", color: "#166534", label: "Завршено" },
    failed: { bg: "#FEE2E2", color: "#991B1B", label: "Неуспешно" },
  };
  const s = map[status] || map.pending;
  return (
    <span style={{ background: s.bg, color: s.color, padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 600, whiteSpace: "nowrap" }}>
      {s.label}
    </span>
  );
}

function FactList({ title, facts, unknown }) {
  return (
    <div style={{ marginBottom: "10px" }}>
      <p style={{ fontSize: "11px", fontWeight: 700, color: "#64748B", textTransform: "uppercase", margin: "0 0 6px" }}>{title}</p>
      {unknown || facts.length === 0 ? (
        <p style={{ fontSize: "12px", color: "#94A3B8", margin: 0 }}>Непознато — нема потврдени податоци.</p>
      ) : (
        <ul style={{ margin: 0, paddingLeft: "18px" }}>
          {facts.map((f) => (
            <li key={f.id} style={{ fontSize: "12px", color: "#334155", marginBottom: "8px" }}>
              <div>
                {f.text}
                {f.conflicting && (
                  <span style={{ marginLeft: "6px", fontSize: "10px", fontWeight: 700, color: "#B45309" }}>⚠ конфликтен податок</span>
                )}
              </div>
              <div style={{ fontSize: "11px", color: "#94A3B8", marginTop: "2px" }}>
                „{f.excerpt}" —{" "}
                <a href={f.source_url} target="_blank" rel="noreferrer" style={{ color: "#2563EB" }}>
                  извор
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ResearchCard({ lead, onChanged }) {
  const [research, setResearch] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    let ignore = false;
    fetch(`${API_BASE}/leads/${lead.id}/research`)
      .then((res) => res.json())
      .then((data) => {
        if (!ignore) setResearch(data);
      });
    return () => {
      ignore = true; // discard this request's result if `lead.id` changes before it resolves
    };
  }, [lead.id]);

  const runResearch = async (forceRefresh) => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/leads/${lead.id}/research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force_refresh: forceRefresh }),
      });
      if (!res.ok) {
        const body = await res.json();
        setError(body.detail || "Research failed.");
      } else {
        setResearch(await res.json());
      }
      await onChanged?.();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ background: "white", borderRadius: "12px", padding: "1.2rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.6rem" }}>
        <div>
          <p style={{ margin: 0, fontWeight: 700, fontSize: "14px", color: "#1E3A5F" }}>{lead.name} — {lead.company}</p>
          <p style={{ margin: "2px 0 0", fontSize: "12px", color: "#64748B" }}>{lead.website || "нема website наведен"}</p>
        </div>
        {research && <ResearchStatusPill status={research.status} />}
      </div>

      {!lead.website ? (
        <p style={{ fontSize: "12px", color: "#B45309" }}>
          Потребен е експлицитен website во CSV увозот за овој лид — истражувањето никогаш не погодува домен.
        </p>
      ) : (
        <div style={{ display: "flex", gap: "8px", marginBottom: "10px" }}>
          <button disabled={loading} onClick={() => runResearch(false)} style={btnStyle(!loading, "#7C3AED")}>
            {loading ? "⏳ Работи..." : research?.status === "completed" ? "Прикажи (кеш)" : "▶ Истражи"}
          </button>
          <button disabled={loading} onClick={() => runResearch(true)} style={btnStyle(!loading, "#0F766E")}>
            🔄 Освежи (force refresh)
          </button>
        </div>
      )}

      {error && <p style={{ fontSize: "12px", color: "#991B1B" }}>❌ {error}</p>}

      {research && research.status === "failed" && (
        <p style={{ fontSize: "12px", color: "#991B1B" }}>❌ Грешка: {research.error}</p>
      )}

      {research && research.last_refresh_error && (
        <p style={{ fontSize: "11px", color: "#B45309" }}>
          ⚠ Последното освежување не успеа ({research.last_refresh_error}) — прикажани се претходните резултати.
        </p>
      )}

      {research && research.status === "completed" && (
        <>
          {research.has_conflicts && (
            <p style={{ fontSize: "11px", color: "#B45309", fontWeight: 600 }}>
              ⚠ Пронајдени се спротивставени податоци од различни страници (означени подолу).
            </p>
          )}
          <FactList title="Понуда / производи" facts={research.offerings} unknown={research.offerings_unknown} />
          <FactList title="Целни клиенти" facts={research.target_customers} unknown={research.target_customers_unknown} />
          <FactList title="Други факти" facts={research.other_facts} unknown={research.other_facts.length === 0} />
          <p style={{ fontSize: "11px", color: "#94A3B8" }}>
            {research.pages_fetched} страници проверени · важи до {research.expires_at ? new Date(research.expires_at).toLocaleString() : "—"}
          </p>
        </>
      )}
    </div>
  );
}

function parseListInput(value) {
  return value
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function CriteriaEditor({ campaignId, currentCriteria, onSaved }) {
  const [productService, setProductService] = useState(currentCriteria?.product_service || "");
  const [industries, setIndustries] = useState((currentCriteria?.target_industries || []).join(", "));
  const [countries, setCountries] = useState((currentCriteria?.target_countries || []).join(", "));
  const [companySize, setCompanySize] = useState(currentCriteria?.preferred_company_size || "");
  const [needs, setNeeds] = useState((currentCriteria?.business_needs || []).join(", "));
  const [exclusions, setExclusions] = useState((currentCriteria?.exclusion_criteria || []).join(", "));
  const [weights, setWeights] = useState(
    currentCriteria?.weights || { industry: 1, country: 1, company_size: 1, business_needs: 1 }
  );
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      await fetch(`${API_BASE}/campaigns/${campaignId}/criteria`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          product_service: productService,
          target_industries: parseListInput(industries),
          target_countries: parseListInput(countries),
          preferred_company_size: companySize,
          business_needs: parseListInput(needs),
          exclusion_criteria: parseListInput(exclusions),
          weights,
        }),
      });
      await onSaved();
    } finally {
      setSaving(false);
    }
  };

  const inputStyle = {
    width: "100%",
    boxSizing: "border-box",
    padding: "8px 10px",
    fontSize: "13px",
    border: "1.5px solid #E2E8F0",
    borderRadius: "8px",
    marginBottom: "10px",
  };
  const labelStyle = { fontSize: "11px", fontWeight: 700, color: "#64748B", textTransform: "uppercase", marginBottom: "4px", display: "block" };

  return (
    <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1.5rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <p style={{ fontSize: "11px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", letterSpacing: "0.08em", margin: 0 }}>
          Критериуми за квалификација {currentCriteria && `(тековна верзија: ${currentCriteria.version})`}
        </p>
      </div>

      <label style={labelStyle}>Производ / услуга што се нуди</label>
      <textarea value={productService} onChange={(e) => setProductService(e.target.value)} rows={2} style={{ ...inputStyle, fontFamily: "inherit" }} />

      <label style={labelStyle}>Целни индустрии (одделени со запирка)</label>
      <input value={industries} onChange={(e) => setIndustries(e.target.value)} style={inputStyle} placeholder="IT, Finance" />

      <label style={labelStyle}>Целни земји (одделени со запирка)</label>
      <input value={countries} onChange={(e) => setCountries(e.target.value)} style={inputStyle} placeholder="Macedonia, Serbia" />

      <label style={labelStyle}>Преферирана големина на компанија</label>
      <input value={companySize} onChange={(e) => setCompanySize(e.target.value)} style={inputStyle} placeholder="на пр. 50-200 вработени" />

      <label style={labelStyle}>Деловни потреби (клучни зборови, одделени со запирка)</label>
      <input value={needs} onChange={(e) => setNeeds(e.target.value)} style={inputStyle} placeholder="автоматизација, извештаи" />

      <label style={labelStyle}>Критериуми за исклучување (клучни зборови, одделени со запирка)</label>
      <input value={exclusions} onChange={(e) => setExclusions(e.target.value)} style={inputStyle} placeholder="конкурент, стечај" />

      <label style={labelStyle}>Тежини на критериуми</label>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: "8px", marginBottom: "12px" }}>
        {["industry", "country", "company_size", "business_needs"].map((k) => (
          <div key={k}>
            <span style={{ fontSize: "11px", color: "#64748B" }}>{k}</span>
            <input
              type="number"
              step="0.1"
              value={weights[k] ?? 1}
              onChange={(e) => setWeights({ ...weights, [k]: Number(e.target.value) })}
              style={{ ...inputStyle, marginBottom: 0 }}
            />
          </div>
        ))}
      </div>

      <button disabled={saving} onClick={save} style={btnStyle(!saving, "#2563EB")}>
        {saving ? "⏳ Зачувува..." : "💾 Зачувај нова верзија"}
      </button>
    </div>
  );
}

function EvidenceList({ title, entries, color }) {
  if (entries.length === 0) return null;
  return (
    <div style={{ marginBottom: "8px" }}>
      <p style={{ fontSize: "11px", fontWeight: 700, color, textTransform: "uppercase", margin: "0 0 4px" }}>
        {title} ({entries.length})
      </p>
      <ul style={{ margin: 0, paddingLeft: "18px" }}>
        {entries.map((e, i) => (
          <li key={i} style={{ fontSize: "12px", color: "#334155", marginBottom: "4px" }}>
            {e.detail}
            {e.source?.source_url && (
              <>
                {" "}
                <a href={e.source.source_url} target="_blank" rel="noreferrer" style={{ color: "#2563EB" }}>
                  извор
                </a>
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function QualificationCard({ row, onChanged }) {
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const q = row.qualification;

  const requalify = async () => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/leads/${row.id}/qualify`, { method: "POST" });
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const addLabel = async (label) => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}/leads/${row.id}/qualification/labels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label, notes: notes || null }),
      });
      setNotes("");
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ background: "white", borderRadius: "12px", padding: "1.2rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.6rem", flexWrap: "wrap", gap: "8px" }}>
        <div>
          <p style={{ margin: 0, fontWeight: 700, fontSize: "14px", color: "#1E3A5F" }}>{row.name} — {row.company}</p>
          <p style={{ margin: "2px 0 0", fontSize: "12px", color: "#64748B" }}>{row.industry} · {row.country}</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {q?.is_stale && (
            <span style={{ background: "#FEF3C7", color: "#92400E", padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 600 }}>
              ⚠ застарено
            </span>
          )}
          {row.current_label && (
            <span
              style={{
                background: row.current_label === "suitable" ? "#DCFCE7" : row.current_label === "unsuitable" ? "#FEE2E2" : "#F1F5F9",
                color: row.current_label === "suitable" ? "#166534" : row.current_label === "unsuitable" ? "#991B1B" : "#475569",
                padding: "3px 10px",
                borderRadius: "999px",
                fontSize: "11px",
                fontWeight: 600,
              }}
            >
              {row.current_label === "suitable" ? "✅ Соодветен" : row.current_label === "unsuitable" ? "❌ Несоодветен" : "❔ Несигурно"}
            </span>
          )}
          <span style={{ fontSize: "20px", fontWeight: 700, color: "#2563EB" }}>{q && q.fit_score !== null ? `${q.fit_score.toFixed(0)}` : "—"}</span>
        </div>
      </div>

      {!q ? (
        <p style={{ fontSize: "12px", color: "#94A3B8" }}>Сè уште не е квалификуван.</p>
      ) : (
        <>
          <p style={{ fontSize: "11px", color: "#94A3B8", marginBottom: "8px" }}>
            fit score (не е веројатност за конверзија) · покриеност со докази: {(q.evidence_coverage * 100).toFixed(0)}% · критериуми v{q.criteria_version} · рубрика {q.rubric_version}
          </p>
          <EvidenceList title="Совпаѓа" entries={q.matched} color="#166534" />
          <EvidenceList title="Не се совпаѓа" entries={q.unmatched} color="#B45309" />
          <EvidenceList title="Непознато" entries={q.unknown} color="#64748B" />
          <EvidenceList title="⚠ Исклучувања" entries={q.exclusions} color="#991B1B" />
        </>
      )}

      <div style={{ display: "flex", gap: "8px", marginTop: "10px", flexWrap: "wrap", alignItems: "center" }}>
        <button disabled={busy} onClick={requalify} style={btnStyle(!busy, "#7C3AED")}>
          🔄 (Пре)квалификувај
        </button>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="белешка (опционално)"
          style={{ flex: 1, minWidth: "160px", padding: "7px 10px", fontSize: "12px", border: "1.5px solid #E2E8F0", borderRadius: "8px" }}
        />
        <button disabled={busy} onClick={() => addLabel("suitable")} style={btnStyle(!busy, "#16A34A")}>
          Соодветен
        </button>
        <button disabled={busy} onClick={() => addLabel("unsuitable")} style={btnStyle(!busy, "#DC2626")}>
          Несоодветен
        </button>
        <button disabled={busy} onClick={() => addLabel("unsure")} style={btnStyle(!busy, "#64748B")}>
          Несигурно
        </button>
      </div>
    </div>
  );
}

function QualificationPanel({ campaignId, leads, onChanged }) {
  const [criteriaHistory, setCriteriaHistory] = useState([]);
  const [rows, setRows] = useState([]);
  const [sortBy, setSortBy] = useState("fit_score_desc");
  const [filterLabel, setFilterLabel] = useState("all");
  const [qualifyingAll, setQualifyingAll] = useState(false);

  const load = async () => {
    const [criteriaRes, rowsRes] = await Promise.all([
      fetch(`${API_BASE}/campaigns/${campaignId}/criteria`),
      fetch(`${API_BASE}/campaigns/${campaignId}/qualifications`),
    ]);
    setCriteriaHistory(await criteriaRes.json());
    setRows(await rowsRes.json());
  };

  useEffect(() => {
    let ignore = false;
    (async () => {
      const [criteriaRes, rowsRes] = await Promise.all([
        fetch(`${API_BASE}/campaigns/${campaignId}/criteria`),
        fetch(`${API_BASE}/campaigns/${campaignId}/qualifications`),
      ]);
      const criteriaData = await criteriaRes.json();
      const rowsData = await rowsRes.json();
      if (!ignore) {
        setCriteriaHistory(criteriaData);
        setRows(rowsData);
      }
    })();
    return () => {
      ignore = true;
    };
  }, [campaignId, leads]);

  const qualifyAll = async () => {
    setQualifyingAll(true);
    try {
      await fetch(`${API_BASE}/campaigns/${campaignId}/qualify`, { method: "POST" });
      await load();
      await onChanged?.();
    } finally {
      setQualifyingAll(false);
    }
  };

  const currentCriteria = criteriaHistory[criteriaHistory.length - 1] || null;

  const filtered = rows.filter((r) => {
    if (filterLabel === "all") return true;
    if (filterLabel === "unlabeled") return !r.current_label;
    if (filterLabel === "stale") return r.qualification?.is_stale;
    return r.current_label === filterLabel;
  });

  const sorted = [...filtered].sort((a, b) => {
    const scoreA = a.qualification?.fit_score ?? -1;
    const scoreB = b.qualification?.fit_score ?? -1;
    return sortBy === "fit_score_desc" ? scoreB - scoreA : scoreA - scoreB;
  });

  return (
    <div>
      <CriteriaEditor campaignId={campaignId} currentCriteria={currentCriteria} onSaved={load} />

      <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap" }}>
        <button disabled={qualifyingAll} onClick={qualifyAll} style={btnStyle(!qualifyingAll, "#2563EB")}>
          {qualifyingAll ? "⏳ Квалификува..." : "🎯 Квалификувај ги сите лидови"}
        </button>
        <select value={sortBy} onChange={(e) => setSortBy(e.target.value)} style={{ padding: "8px 12px", borderRadius: "8px", border: "1.5px solid #BFDBFE", fontSize: "13px" }}>
          <option value="fit_score_desc">Score: најголем прв</option>
          <option value="fit_score_asc">Score: најмал прв</option>
        </select>
        <select value={filterLabel} onChange={(e) => setFilterLabel(e.target.value)} style={{ padding: "8px 12px", borderRadius: "8px", border: "1.5px solid #BFDBFE", fontSize: "13px" }}>
          <option value="all">Сите</option>
          <option value="unlabeled">Без ознака</option>
          <option value="stale">Застарени</option>
          <option value="suitable">Соодветни</option>
          <option value="unsuitable">Несоодветни</option>
          <option value="unsure">Несигурни</option>
        </select>
        <span style={{ fontSize: "12px", color: "#64748B" }}>{sorted.length} резултати</span>
      </div>

      {sorted.length === 0 ? (
        <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
          <p>Нема резултати за прикажување.</p>
        </div>
      ) : (
        sorted.map((row) => <QualificationCard key={row.id} row={row} onChanged={load} />)
      )}
    </div>
  );
}

const JOB_TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled", "needs_review"]);

const JOB_STATUS_COLORS = {
  queued: { bg: "#F1F5F9", color: "#475569", label: "На чекање" },
  running: { bg: "#DBEAFE", color: "#1E40AF", label: "Се извршува" },
  succeeded: { bg: "#DCFCE7", color: "#166534", label: "Завршено" },
  failed: { bg: "#FEE2E2", color: "#991B1B", label: "Неуспешно" },
  cancelled: { bg: "#F1F5F9", color: "#64748B", label: "Прекинато" },
  needs_review: { bg: "#FEF3C7", color: "#92400E", label: "Потребен преглед" },
};

function JobStatusPill({ status }) {
  const s = JOB_STATUS_COLORS[status] || JOB_STATUS_COLORS.queued;
  return (
    <span style={{ background: s.bg, color: s.color, padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 600, whiteSpace: "nowrap" }}>
      {s.label}
    </span>
  );
}

function JobStepRow({ step, leadsById }) {
  const lead = leadsById[step.lead_id];
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #F1F5FF", fontSize: "12px" }}>
      <span style={{ color: "#334155" }}>
        {lead ? `${lead.name} — ${lead.company}` : `lead #${step.lead_id}`} <span style={{ color: "#94A3B8" }}>({step.step_type})</span>
      </span>
      <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
        {step.attempt_count > 1 && <span style={{ color: "#94A3B8", fontSize: "11px" }}>обид {step.attempt_count}</span>}
        <JobStatusPill status={step.status} />
      </span>
    </div>
  );
}

function JobCard({ job, leadsById, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const isTerminal = JOB_TERMINAL_STATUSES.has(job.status);
  const canCancel = !isTerminal;
  const canRetry = ["failed", "needs_review", "cancelled"].includes(job.status);
  const notDispatched = job.status === "queued" && !job.dispatched && job.dispatch_attempts > 0;

  const doAction = async (path) => {
    setBusy(true);
    try {
      await fetch(`${API_BASE}${path}`, { method: "POST" });
      await onChanged();
    } finally {
      setBusy(false);
    }
  };

  const succeededCount = job.steps.filter((s) => s.status === "succeeded").length;

  return (
    <div style={{ background: "white", borderRadius: "12px", padding: "1rem 1.2rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: "8px" }}>
        <div>
          <p style={{ margin: 0, fontWeight: 700, fontSize: "13px", color: "#1E3A5F" }}>
            Job #{job.id} — {job.job_type} <span style={{ fontWeight: 400, color: "#94A3B8" }}>({succeededCount}/{job.steps.length} чекори завршени)</span>
          </p>
          <p style={{ margin: "2px 0 0", fontSize: "11px", color: "#94A3B8" }}>{new Date(job.created_at).toLocaleString()}</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          <JobStatusPill status={job.status} />
          {canCancel && (
            <button disabled={busy} onClick={() => doAction(`/jobs/${job.id}/cancel`)} style={btnStyle(!busy, "#DC2626")}>
              Прекини
            </button>
          )}
          {canRetry && (
            <button disabled={busy} onClick={() => doAction(`/jobs/${job.id}/retry`)} style={btnStyle(!busy, "#7C3AED")}>
              Обиди се повторно
            </button>
          )}
          <button onClick={() => setExpanded(!expanded)} style={{ background: "none", border: "none", color: "#2563EB", fontSize: "12px", cursor: "pointer" }}>
            {expanded ? "▲ Сокриј чекори" : "▼ Прикажи чекори"}
          </button>
        </div>
      </div>

      {notDispatched && (
        <p style={{ fontSize: "11px", color: "#B45309", marginTop: "8px" }}>
          ⚠ Не е испратено до worker по {job.dispatch_attempts} обид(и) — провери дали Redis/Celery worker-от работи.
        </p>
      )}

      {expanded && (
        <div style={{ marginTop: "10px" }}>
          {job.steps.map((s) => (
            <JobStepRow key={s.id} step={s} leadsById={leadsById} />
          ))}
          {job.steps.some((s) => s.last_error) && (
            <div style={{ marginTop: "8px" }}>
              {job.steps.filter((s) => s.last_error).map((s) => (
                <p key={s.id} style={{ fontSize: "11px", color: "#991B1B", margin: "4px 0" }}>
                  lead #{s.lead_id} ({s.step_type}): {s.last_error}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ProcessingPanel({ campaignId, leads }) {
  const [jobs, setJobs] = useState([]);
  const [language, setLanguage] = useState("en");
  const [tone, setTone] = useState("professional");
  const [length, setLength] = useState("medium");
  const [starting, setStarting] = useState(false);

  const leadsById = Object.fromEntries(leads.map((l) => [l.id, l]));

  const load = async () => {
    const res = await fetch(`${API_BASE}/campaigns/${campaignId}/jobs`);
    setJobs(await res.json());
  };

  // Poll while any job is still active; stop polling once everything is
  // terminal, and always clean up the interval on unmount or when the
  // campaign changes -- this is the one place in the app that needs a
  // recurring timer, so it's handled locally rather than via a shared effect.
  useEffect(() => {
    let ignore = false;
    let intervalId = null;

    const tick = async () => {
      const res = await fetch(`${API_BASE}/campaigns/${campaignId}/jobs`);
      const data = await res.json();
      if (ignore) return;
      setJobs(data);
      const stillActive = data.some((j) => !JOB_TERMINAL_STATUSES.has(j.status));
      if (!stillActive && intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      }
    };

    tick();
    intervalId = setInterval(tick, 3000);

    return () => {
      ignore = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [campaignId]);

  const startWorkflow = async () => {
    setStarting(true);
    try {
      await fetch(`${API_BASE}/campaigns/${campaignId}/jobs/workflow`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language, tone, length }),
      });
      await load();
    } finally {
      setStarting(false);
    }
  };

  return (
    <div>
      <p style={{ fontSize: "12px", color: "#64748B", marginBottom: "1rem" }}>
        Обработката работи во позадина (research → квалификација → предлог) и застанува пред одобрување — никогаш не одобрува
        или праќа автоматски. Ако Redis/Celery worker-от не работи, job-от ќе остане "на чекање" и тоа ќе биде јасно означено.
      </p>
      <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "1.2rem", flexWrap: "wrap" }}>
        <select value={language} onChange={(e) => setLanguage(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="en">English</option>
          <option value="mk">Македонски</option>
        </select>
        <select value={tone} onChange={(e) => setTone(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="professional">professional</option>
          <option value="friendly">friendly</option>
          <option value="formal">formal</option>
          <option value="casual">casual</option>
        </select>
        <select value={length} onChange={(e) => setLength(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
          <option value="short">short</option>
          <option value="medium">medium</option>
          <option value="long">long</option>
        </select>
        <button disabled={starting || !campaignId} onClick={startWorkflow} style={btnStyle(!starting && campaignId, "#2563EB")}>
          {starting ? "⏳ Стартува..." : "▶ Стартувај workflow (сите лидови)"}
        </button>
      </div>

      {jobs.length === 0 ? (
        <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
          <p>Нема job-ови сè уште за оваа кампања.</p>
        </div>
      ) : (
        jobs.map((job) => <JobCard key={job.id} job={job} leadsById={leadsById} onChanged={load} />)
      )}
    </div>
  );
}

function MetricBadge({ ok, label, na }) {
  const bg = na ? "#F1F5F9" : ok ? "#DCFCE7" : "#FEE2E2";
  const color = na ? "#64748B" : ok ? "#166534" : "#991B1B";
  return (
    <span style={{ background: bg, color, padding: "2px 8px", borderRadius: "999px", fontSize: "10px", fontWeight: 600, whiteSpace: "nowrap" }}>
      {na ? `${label}: n/a` : `${label}: ${ok ? "✓" : "✗"}`}
    </span>
  );
}

function EvalResultCard({ result, onRated }) {
  const [relevance, setRelevance] = useState(result.human_rating_relevance || "");
  const [personalization, setPersonalization] = useState(result.human_rating_personalization || "");
  const [notes, setNotes] = useState(result.human_notes || "");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const res = await fetch(`${API_BASE}/evaluation/results/${result.id}/rating`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          relevance: relevance ? Number(relevance) : null,
          personalization: personalization ? Number(personalization) : null,
          notes,
        }),
      });
      onRated(await res.json());
    } finally {
      setSaving(false);
    }
  };

  const m = result.metrics || {};
  return (
    <div style={{ background: "#F8FAFC", border: "1px solid #E2E8F0", borderRadius: "10px", padding: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
        <span style={{ fontSize: "11px", fontWeight: 700, color: "#64748B", textTransform: "uppercase" }}>
          {result.approach === "basic" ? "Основен prompt (без evidence)" : "Evidence-based workflow"}
        </span>
        {result.error && <span style={{ fontSize: "11px", color: "#991B1B", fontWeight: 700 }}>ГРЕШКА</span>}
      </div>

      {result.error ? (
        <p style={{ fontSize: "12px", color: "#991B1B" }}>{result.error}</p>
      ) : (
        <>
          <p style={{ fontSize: "13px", fontWeight: 700, color: "#1E3A5F", margin: "0 0 4px" }}>{result.subject}</p>
          <p style={{ fontSize: "12px", color: "#334155", whiteSpace: "pre-wrap", marginBottom: "10px" }}>{result.body}</p>

          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px" }}>
            <MetricBadge ok={m.schema_valid} label="Шема" />
            <MetricBadge ok={m.source_refs?.applicable ? m.source_refs.invalid_count === 0 : null} na={!m.source_refs?.applicable} label="Извори" />
            <MetricBadge ok={m.language?.compliant} label="Јазик" />
            <MetricBadge ok={m.length?.compliant} label="Должина" />
            <MetricBadge ok={m.unsupported_claims?.count === 0} label={`Неоснован. тврдења (${m.unsupported_claims?.count ?? 0})`} />
          </div>
          {result.latency_ms == null && result.total_tokens == null && (
            <p style={{ fontSize: "10px", color: "#94A3B8", marginBottom: "10px" }}>Латенција/токени: недостапно (mock run или не е мерено)</p>
          )}
        </>
      )}

      <div style={{ borderTop: "1px dashed #CBD5E1", paddingTop: "10px", marginTop: "6px" }}>
        <p style={{ fontSize: "10px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", marginBottom: "6px" }}>
          Човечка оценка {result.evaluated ? "" : "— Не е оценето"}
        </p>
        <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
          <select value={relevance} onChange={(e) => setRelevance(e.target.value)} style={{ padding: "5px 8px", borderRadius: "6px", border: "1px solid #CBD5E1", fontSize: "11px" }}>
            <option value="">Релевантност (1-5)</option>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <select value={personalization} onChange={(e) => setPersonalization(e.target.value)} style={{ padding: "5px 8px", borderRadius: "6px", border: "1px solid #CBD5E1", fontSize: "11px" }}>
            <option value="">Персонализација (1-5)</option>
            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </div>
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="Белешки..." rows={2} style={{ width: "100%", marginTop: "6px", padding: "6px 8px", borderRadius: "6px", border: "1px solid #CBD5E1", fontSize: "11px", boxSizing: "border-box" }} />
        <button onClick={save} disabled={saving} style={{ marginTop: "6px", background: "#2563EB", color: "white", border: "none", borderRadius: "6px", padding: "5px 14px", fontSize: "11px", fontWeight: 600, cursor: "pointer" }}>
          {saving ? "Зачувува..." : "Зачувај оценка"}
        </button>
      </div>
    </div>
  );
}

function EvaluationTab() {
  const [dataset, setDataset] = useState(null);
  const [runs, setRuns] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runData, setRunData] = useState(null);
  const [loading, setLoading] = useState(false);

  const loadRuns = async () => {
    const [dsRes, runsRes] = await Promise.all([fetch(`${API_BASE}/evaluation/dataset`), fetch(`${API_BASE}/evaluation/runs`)]);
    setDataset(await dsRes.json());
    const runsData = await runsRes.json();
    setRuns(runsData);
    if (runsData.length > 0 && !selectedRunId) setSelectedRunId(runsData[0].id);
  };

  useEffect(() => {
    let ignore = false;
    (async () => {
      const [dsRes, runsRes] = await Promise.all([fetch(`${API_BASE}/evaluation/dataset`), fetch(`${API_BASE}/evaluation/runs`)]);
      const ds = await dsRes.json();
      const runsData = await runsRes.json();
      if (ignore) return;
      setDataset(ds);
      setRuns(runsData);
      if (runsData.length > 0) setSelectedRunId((prev) => prev ?? runsData[0].id);
    })();
    return () => { ignore = true; };
  }, []);

  useEffect(() => {
    let ignore = false;
    (async () => {
      if (!selectedRunId) {
        if (!ignore) setRunData(null);
        return;
      }
      if (!ignore) setLoading(true);
      try {
        const res = await fetch(`${API_BASE}/evaluation/runs/${selectedRunId}/results`);
        const data = await res.json();
        if (!ignore) setRunData(data);
      } finally {
        if (!ignore) setLoading(false);
      }
    })();
    return () => { ignore = true; };
  }, [selectedRunId]);

  const updateResult = (updated) => {
    setRunData((prev) => ({ ...prev, results: prev.results.map((r) => (r.id === updated.id ? updated : r)) }));
  };

  const grouped = {};
  (runData?.results || []).forEach((r) => {
    grouped[r.case_id] = grouped[r.case_id] || { case_id: r.case_id, tags: r.case_tags, results: [] };
    grouped[r.case_id].results.push(r);
  });

  return (
    <div>
      <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", marginBottom: "1.5rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
        <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#1E3A5F", margin: "0 0 8px" }}>🧪 Евалуација: основен prompt vs. evidence-based workflow</h2>
        {dataset && (
          <p style={{ fontSize: "12px", color: "#64748B", margin: "0 0 10px" }}>
            Датасет <strong>{dataset.version}</strong> — {dataset.case_count} чисто синтетички случаи (не се реални лидови). Понуда на кампањата: <em>{dataset.campaign_offering}</em>
          </p>
        )}
        <p style={{ fontSize: "12px", color: "#94A3B8", margin: 0 }}>
          Нови evaluation "run"-ови се стартуваат од командна линија, никогаш автоматски од UI-то (за да не се направат случајни платени повици): <code>cd app &amp;&amp; ../venv/Scripts/python.exe -m evaluation.run_eval</code> (offline/mock, бесплатно) или со <code>--mode live --max-calls N</code> за реален, буџетиран run.
        </p>
      </div>

      <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "1.2rem", flexWrap: "wrap" }}>
          <span style={{ fontSize: "13px", fontWeight: 700, color: "#1E3A5F" }}>Run:</span>
          <select value={selectedRunId || ""} onChange={(e) => setSelectedRunId(Number(e.target.value))} style={{ padding: "6px 10px", borderRadius: "8px", border: "1.5px solid #BFDBFE", fontSize: "12px" }}>
            {runs.length === 0 && <option value="">Нема зачувани run-ови</option>}
            {runs.map((r) => (
              <option key={r.id} value={r.id}>
                #{r.id} — {r.mode === "mock" ? "MOCK" : "LIVE"} — {new Date(r.created_at).toLocaleString()} ({r.result_count} резултати)
              </option>
            ))}
          </select>
          <button onClick={loadRuns} style={{ background: "white", color: "#2563EB", border: "1.5px solid #2563EB", borderRadius: "8px", padding: "6px 14px", fontSize: "12px", fontWeight: 600, cursor: "pointer" }}>
            ⟳ Освежи
          </button>
          {runData?.run && (
            <span style={{ background: runData.run.mode === "mock" ? "#FEF9C3" : "#DCFCE7", color: runData.run.mode === "mock" ? "#854D0E" : "#166534", padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 700 }}>
              {runData.run.mode === "mock" ? "⚠️ MOCK RUN — само верификација на harness-от, не мери реален квалитет" : "✅ LIVE RUN — реални model повици"}
            </span>
          )}
        </div>

        {runs.length === 0 && (
          <div style={{ textAlign: "center", padding: "2rem", color: "#94A3B8" }}>
            <p>Нема evaluation run-ови сè уште. Стартувај еден од командна линија (гледај упатството погоре).</p>
          </div>
        )}

        {loading && <p style={{ fontSize: "13px", color: "#94A3B8" }}>Вчитува...</p>}

        {Object.values(grouped).map((g) => (
          <div key={g.case_id} style={{ marginBottom: "1.6rem", borderBottom: "1px solid #EFF6FF", paddingBottom: "1.6rem" }}>
            <h3 style={{ fontSize: "14px", fontWeight: 700, color: "#1E3A5F", marginBottom: "10px" }}>
              {g.case_id} <span style={{ fontWeight: 400, color: "#94A3B8" }}>({g.tags.join(", ")})</span>
            </h3>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: "12px" }}>
              {g.results.map((r) => (
                <EvalResultCard key={r.id} result={r} onRated={updateResult} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function App() {
  const [campaigns, setCampaigns] = useState([]);
  const [campaignId, setCampaignId] = useState(null);
  const [newCampaignName, setNewCampaignName] = useState("");
  const [leads, setLeads] = useState([]);
  const [stats, setStats] = useState(null);
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("dashboard");
  const [research, setResearch] = useState([]);
  const [resLoading, setResLoading] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [generating, setGenerating] = useState(false);
  const [bulkLanguage, setBulkLanguage] = useState("en");
  const [bulkTone, setBulkTone] = useState("professional");
  const [bulkLength, setBulkLength] = useState("medium");
  const [dryRun, setDryRun] = useState(true);
  const [sendResult, setSendResult] = useState(null);
  const [sending, setSending] = useState(false);

  const fetchCampaigns = async () => {
    const res = await fetch(`${API_BASE}/campaigns`);
    const data = await res.json();
    setCampaigns(data);
    if (!campaignId && data.length > 0) setCampaignId(data[0].id);
  };

  const fetchLeads = async (id) => {
    if (!id) { setLeads([]); return; }
    const res = await fetch(`${API_BASE}/campaigns/${id}/leads`);
    setLeads(await res.json());
  };

  const fetchStats = async (id) => {
    const url = id ? `${API_BASE}/stats?campaign_id=${id}` : `${API_BASE}/stats`;
    const res = await fetch(url);
    setStats(await res.json());
  };

  const refreshAll = async () => {
    await Promise.all([fetchLeads(campaignId), fetchStats(campaignId), fetchCampaigns()]);
  };

  const hasAutoSelectedCampaign = useRef(false);

  useEffect(() => {
    let ignore = false;
    (async () => {
      const res = await fetch(`${API_BASE}/campaigns`);
      const data = await res.json();
      if (ignore) return;
      setCampaigns(data);
      if (!hasAutoSelectedCampaign.current && data.length > 0) {
        hasAutoSelectedCampaign.current = true;
        setCampaignId(data[0].id);
      }
    })();
    return () => {
      ignore = true;
    };
  }, []);

  useEffect(() => {
    let ignore = false;
    (async () => {
      const leadsData = campaignId ? await (await fetch(`${API_BASE}/campaigns/${campaignId}/leads`)).json() : [];
      if (!ignore) setLeads(leadsData);

      const statsUrl = campaignId ? `${API_BASE}/stats?campaign_id=${campaignId}` : `${API_BASE}/stats`;
      const statsData = await (await fetch(statsUrl)).json();
      if (!ignore) setStats(statsData);
    })();
    return () => {
      ignore = true; // a superseded campaign switch must not overwrite the newer selection's data
    };
  }, [campaignId]);

  const createCampaign = async () => {
    const name = newCampaignName.trim();
    if (!name) return;
    const res = await fetch(`${API_BASE}/campaigns`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await res.json();
    setNewCampaignName("");
    await fetchCampaigns();
    setImportResult(null);
    setSendResult(null);
    setCampaignId(data.id);
  };

  const handleUpload = async (e) => {
    const file = e.target.files[0];
    if (!file || !campaignId) return;
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${API_BASE}/campaigns/${campaignId}/leads/import`, { method: "POST", body: formData });
    const data = await res.json();
    setImportResult({ ...data, ok: res.ok, fileName: file.name });
    e.target.value = "";
    if (res.ok) await refreshAll();
  };

  const generateDrafts = async () => {
    setGenerating(true);
    try {
      await fetch(`${API_BASE}/campaigns/${campaignId}/drafts/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ language: bulkLanguage, tone: bulkTone, length: bulkLength }),
      });
      await refreshAll();
    } finally {
      setGenerating(false);
    }
  };

  const sendCampaign = async () => {
    setSending(true);
    try {
      const res = await fetch(`${API_BASE}/campaigns/${campaignId}/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dry_run: dryRun }),
      });
      setSendResult(await res.json());
      await refreshAll();
    } finally {
      setSending(false);
    }
  };

  const runResearch = async () => {
    setResLoading(true);
    try {
      const res = await fetch(`${API_BASE}/research?campaign_id=${campaignId}`, { method: "POST" });
      const data = await res.json();
      setResearch(data.results || []);
    } finally {
      setResLoading(false);
    }
  };

  const filtered = leads.filter(
    (l) =>
      (l.name || "").toLowerCase().includes(search.toLowerCase()) ||
      (l.company || "").toLowerCase().includes(search.toLowerCase())
  );

  const industries = stats ? Object.entries(stats.by_industry || {}) : [];
  const maxIndustryCount = Math.max(1, ...industries.map(([, c]) => c));

  const statCards = stats
    ? [
        { icon: "👥", val: stats.total_leads, lbl: "Вкупно лидови", bg: "linear-gradient(135deg,#2563EB,#3B82F6)" },
        { icon: "✅", val: stats.approved, lbl: "Одобрени", bg: "linear-gradient(135deg,#16A34A,#22C55E)" },
        { icon: "📧", val: stats.sent + stats.dry_run_sent, lbl: "Испратени (реално + dry-run)", bg: "linear-gradient(135deg,#7C3AED,#A78BFA)" },
        { icon: "👁️", val: stats.tracking_available?.opened ? stats.opened : "—", lbl: stats.tracking_available?.opened ? "Отворени" : "Отворени (нема следење)", bg: "linear-gradient(135deg,#D97706,#F59E0B)" },
      ]
    : [];

  return (
    <div style={{ background: "#EFF6FF", minHeight: "100vh", fontFamily: "Inter, sans-serif", padding: "2rem" }}>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem", flexWrap: "wrap", gap: "10px" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 700, color: "#1E3A5F", margin: 0 }}>📤 B2B Аутрич Dashboard</h1>
          <p style={{ fontSize: "13px", color: "#64748B", margin: "4px 0 0" }}>Кампањи · увоз · одобрување · испраќање (mock/dry-run по default)</p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          {["dashboard", "processing", "drafts", "company-research", "qualification", "prompts", "evaluation"].map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              style={{
                background: tab === t ? "#2563EB" : "white",
                color: tab === t ? "white" : "#2563EB",
                border: "1.5px solid #2563EB",
                borderRadius: "8px",
                padding: "8px 18px",
                fontSize: "13px",
                fontWeight: 600,
                cursor: "pointer",
              }}
            >
              {
                {
                  dashboard: "Dashboard",
                  processing: "⚙️ Обработка",
                  drafts: "📝 Предлози",
                  "company-research": "🔎 Компаниско истражување",
                  qualification: "🎯 Квалификација",
                  prompts: "🔬 Prompt-и (експеримент)",
                  evaluation: "🧪 Евалуација",
                }[t]
              }
            </button>
          ))}
        </div>
      </div>

      {/* Campaign selector */}
      <div style={{ background: "white", borderRadius: "12px", padding: "1rem 1.4rem", marginBottom: "1.5rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "13px", fontWeight: 700, color: "#1E3A5F" }}>Кампања:</span>
        <select
          value={campaignId || ""}
          onChange={(e) => {
            setImportResult(null);
            setSendResult(null);
            setCampaignId(Number(e.target.value));
          }}
          style={{ padding: "8px 12px", borderRadius: "8px", border: "1.5px solid #BFDBFE", fontSize: "13px" }}
        >
          {campaigns.length === 0 && <option value="">Нема кампањи</option>}
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>{c.name} ({c.lead_count} лидови)</option>
          ))}
        </select>
        <input
          value={newCampaignName}
          onChange={(e) => setNewCampaignName(e.target.value)}
          placeholder="Име на нова кампања"
          style={{ padding: "8px 12px", borderRadius: "8px", border: "1.5px solid #BFDBFE", fontSize: "13px", minWidth: "180px" }}
        />
        <button onClick={createCampaign} style={btnStyle(true, "#0F766E")}>+ Нова кампања</button>

        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "10px" }}>
          <label style={{ background: "#0F766E", color: "white", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: campaignId ? "pointer" : "not-allowed", opacity: campaignId ? 1 : 0.5, whiteSpace: "nowrap" }}>
            📂 Увези CSV
            <input type="file" accept=".csv" onChange={handleUpload} disabled={!campaignId} style={{ display: "none" }} />
          </label>
        </div>
      </div>

      {importResult && (
        <div style={{ background: "white", borderRadius: "12px", padding: "1rem 1.4rem", marginBottom: "1.5rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
          {importResult.ok ? (
            <>
              <p style={{ margin: 0, fontSize: "13px", fontWeight: 600, color: "#0F766E" }}>
                ✅ Увезени {importResult.imported} / {importResult.total_rows} редови од "{importResult.fileName}" — {importResult.duplicates} дупликати прескокнати.
              </p>
              {importResult.row_errors?.length > 0 && (
                <div style={{ marginTop: "8px" }}>
                  <p style={{ fontSize: "12px", fontWeight: 700, color: "#B45309", margin: "4px 0" }}>Грешки по ред ({importResult.row_errors.length}):</p>
                  <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "12px", color: "#7C2D12", maxHeight: "140px", overflowY: "auto" }}>
                    {importResult.row_errors.map((e, i) => (
                      <li key={i}>Ред {e.row}: [{e.field}] {e.message}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <p style={{ margin: 0, fontSize: "13px", fontWeight: 600, color: "#991B1B" }}>❌ {importResult.detail}</p>
          )}
        </div>
      )}

      {tab === "dashboard" && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: "16px", marginBottom: "1.5rem" }}>
            {statCards.map((s, i) => (
              <div key={i} style={{ borderRadius: "14px", overflow: "hidden", boxShadow: "0 2px 12px rgba(37,99,235,0.10)" }}>
                <div style={{ background: s.bg, padding: "1.2rem 1.4rem 0.8rem" }}>
                  <div style={{ fontSize: "26px", marginBottom: "4px" }}>{s.icon}</div>
                  <div style={{ fontSize: "30px", fontWeight: 700, color: "white" }}>{s.val}</div>
                </div>
                <div style={{ background: "white", padding: "8px 1.4rem 10px" }}>
                  <div style={{ fontSize: "12px", color: "#64748B", fontWeight: 500 }}>{s.lbl}</div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1.5rem" }}>
            <p style={{ fontSize: "11px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "1.2rem" }}>Лидови по индустрија</p>
            {industries.length === 0 && <p style={{ fontSize: "13px", color: "#94A3B8" }}>Нема податоци.</p>}
            {industries.map(([label, count]) => (
              <div key={label} style={{ marginBottom: "14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
                  <span style={{ fontSize: "13px", color: "#334155", fontWeight: 500 }}>{label}</span>
                  <span style={{ fontSize: "12px", color: "#94A3B8" }}>{count} лидови</span>
                </div>
                <div style={{ height: "8px", background: "#EFF6FF", borderRadius: "4px", overflow: "hidden" }}>
                  <div style={{ width: `${(count / maxIndustryCount) * 100}%`, height: "100%", background: "#2563EB", borderRadius: "4px" }} />
                </div>
              </div>
            ))}
            {stats && !stats.tracking_available?.replied && (
              <p style={{ fontSize: "11px", color: "#94A3B8", marginTop: "1rem" }}>
                ℹ️ Одговорите (replies) не се следат во моментов — нема механизам за трекирање, вредноста намерно не се прикажува како лажна нула.
              </p>
            )}
          </div>

          <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
              <p style={{ fontSize: "11px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", letterSpacing: "0.08em", margin: 0 }}>Лидови</p>
              <span style={{ fontSize: "12px", color: "#64748B" }}>{filtered.length} резултати</span>
            </div>
            <input
              type="text"
              placeholder="🔍  Пребарај по име или компанија..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: "100%", boxSizing: "border-box", padding: "10px 16px", fontSize: "13px", border: "1.5px solid #BFDBFE", borderRadius: "10px", marginBottom: "1rem", background: "#F8FAFF", color: "#1E293B", outline: "none" }}
            />
            {filtered.length === 0 ? (
              <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8" }}>
                <div style={{ fontSize: "40px", marginBottom: "1rem" }}>📭</div>
                <p style={{ fontSize: "14px" }}>Нема лидови. Избери/создади кампања и увези CSV.</p>
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "#F1F5FF" }}>
                    {["Контакт", "Компанија", "Индустрија", "Email", "Website", "Статус"].map((h) => (
                      <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: "11px", color: "#64748B", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((l) => (
                    <tr key={l.id} style={{ borderTop: "1px solid #F1F5FF" }}>
                      <td style={{ padding: "12px 14px", fontSize: "13px", color: "#1E293B", fontWeight: 500 }}>{l.name}</td>
                      <td style={{ padding: "12px 14px", fontSize: "13px", color: "#334155" }}>{l.company}</td>
                      <td style={{ padding: "12px 14px", fontSize: "13px", color: "#334155" }}>{l.industry}</td>
                      <td style={{ padding: "12px 14px", fontSize: "12px", color: l.email ? "#2563EB" : "#CBD5E1", fontWeight: 500 }}>{l.email || "нема"}</td>
                      <td style={{ padding: "12px 14px", fontSize: "12px", color: "#64748B" }}>{l.website || "—"}</td>
                      <td style={{ padding: "12px 14px" }}><StatusPill status={l.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {tab === "processing" && (
        <div>
          {!campaignId ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
              <p>Избери кампања прво.</p>
            </div>
          ) : (
            <ProcessingPanel campaignId={campaignId} leads={leads} />
          )}
        </div>
      )}

      {tab === "drafts" && (
        <div>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", marginBottom: "1.2rem", flexWrap: "wrap" }}>
            <button disabled={!campaignId || generating} onClick={generateDrafts} style={btnStyle(campaignId && !generating, "#7C3AED")}>
              {generating ? "⏳ Генерира..." : "✨ Генерирај предлози за сите лидови"}
            </button>
            <select value={bulkLanguage} onChange={(e) => setBulkLanguage(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
              <option value="en">English</option>
              <option value="mk">Македонски</option>
            </select>
            <select value={bulkTone} onChange={(e) => setBulkTone(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
              <option value="professional">professional</option>
              <option value="friendly">friendly</option>
              <option value="formal">formal</option>
              <option value="casual">casual</option>
            </select>
            <select value={bulkLength} onChange={(e) => setBulkLength(e.target.value)} style={{ padding: "7px 10px", borderRadius: "8px", border: "1.5px solid #E2E8F0", fontSize: "12px" }}>
              <option value="short">short</option>
              <option value="medium">medium</option>
              <option value="long">long</option>
            </select>
            <label style={{ fontSize: "13px", color: "#334155", display: "flex", alignItems: "center", gap: "6px" }}>
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              Dry-run (не праќа реални email-и)
            </label>
            <button disabled={!campaignId || sending} onClick={sendCampaign} style={btnStyle(campaignId && !sending, dryRun ? "#0F766E" : "#DC2626")}>
              {sending ? "⏳ Праќа..." : dryRun ? "▶ Симулирај испраќање" : "🚨 Прати реални email-и"}
            </button>
          </div>

          {sendResult && (
            <div style={{ background: "white", borderRadius: "12px", padding: "1rem 1.4rem", marginBottom: "1.2rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
              <p style={{ margin: 0, fontSize: "13px", fontWeight: 600, color: "#1E3A5F" }}>
                {sendResult.dry_run ? "Симулација" : "Реално испраќање"}: {sendResult.sent} испратени, {sendResult.skipped} прескокнати (нема важечко одобрение), {sendResult.failed} неуспешни.
              </p>
            </div>
          )}

          {leads.length === 0 ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
              <p>Нема лидови во оваа кампања. Увези CSV прво.</p>
            </div>
          ) : (
            leads.map((lead) => (
              <DraftCard key={`${lead.id}-${lead.draft?.version ?? 0}`} lead={lead} campaignId={campaignId} onChanged={refreshAll} />
            ))
          )}
        </div>
      )}

      {tab === "company-research" && (
        <div>
          <p style={{ fontSize: "12px", color: "#64748B", marginBottom: "1rem" }}>
            Истражува само експлицитно наведениот website на секој лид (никогаш не погодува домен). Резултатите се кешираат;
            користи "Освежи" за присилно повторно превземање. Истражувањето никогаш не испраќа email и не влијае на одобрувањето на предлозите.
          </p>
          {leads.length === 0 ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
              <p>Нема лидови во оваа кампања. Увези CSV прво.</p>
            </div>
          ) : (
            leads.map((lead) => <ResearchCard key={lead.id} lead={lead} onChanged={() => fetchLeads(campaignId)} />)
          )}
        </div>
      )}

      {tab === "qualification" && (
        <div>
          <p style={{ fontSize: "12px", color: "#64748B", marginBottom: "1rem" }}>
            Детерминистичка, објаснива квалификација врз основа на валидирани факти од истражувањето и податоците од CSV увозот.
            Score-от е стапка на совпаѓање со тежинските критериуми — <strong>не е веројатност за конверзија</strong>.
          </p>
          {!campaignId ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8", background: "white", borderRadius: "14px" }}>
              <p>Избери кампања прво.</p>
            </div>
          ) : (
            <QualificationPanel campaignId={campaignId} leads={leads} onChanged={() => fetchLeads(campaignId)} />
          )}
        </div>
      )}

      {tab === "prompts" && (
        <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
            <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#1E3A5F", margin: 0 }}>🔬 Споредба на Prompt Техники</h2>
            <button onClick={runResearch} disabled={resLoading || !campaignId} style={{ background: resLoading ? "#C4B5FD" : "#7C3AED", color: "white", border: "none", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}>
              {resLoading ? "⏳ Генерира..." : "▶ Стартувај истражување"}
            </button>
          </div>

          {research.length === 0 ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8" }}>
              <div style={{ fontSize: "40px", marginBottom: "1rem" }}>🔬</div>
              <p>Кликни "Стартувај истражување" за да ги споредиш 3-те prompt техники за лидовите во избраната кампања.</p>
            </div>
          ) : (
            research.map((r, i) => (
              <div key={i} style={{ marginBottom: "2rem", borderBottom: "1px solid #EFF6FF", paddingBottom: "2rem" }}>
                <h3 style={{ fontSize: "15px", fontWeight: 700, color: "#1E3A5F", marginBottom: "1rem" }}>
                  {r.lead} — {r.company}
                </h3>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: "12px" }}>
                  {[
                    { label: "V1 — Основен", msg: r.prompt_v1, color: "#EFF6FF", border: "#BFDBFE" },
                    { label: "V2 — Детален", msg: r.prompt_v2, color: "#F0FDF4", border: "#BBF7D0" },
                    { label: "V3 — Few-shot", msg: r.prompt_v3, color: "#FAF5FF", border: "#E9D5FF" },
                  ].map((p, j) => (
                    <div key={j} style={{ background: p.color, border: `1px solid ${p.border}`, borderRadius: "10px", padding: "1rem" }}>
                      <p style={{ fontSize: "11px", fontWeight: 700, color: "#64748B", textTransform: "uppercase", marginBottom: "8px" }}>{p.label}</p>
                      <p style={{ fontSize: "12px", color: "#334155", lineHeight: "1.6", whiteSpace: "pre-wrap" }}>{p.msg}</p>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {tab === "evaluation" && <EvaluationTab />}

    </div>
  );
}
