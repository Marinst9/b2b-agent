import { useState, useEffect } from "react";

const industries = [
  { label: "IT / Софтвер", count: 18, pct: 75, color: "#2563EB" },
  { label: "Финансии", count: 10, pct: 42, color: "#3B82F6" },
  { label: "Маркетинг", count: 7, pct: 29, color: "#60A5FA" },
  { label: "Е-трговија", count: 5, pct: 21, color: "#93C5FD" },
];

function StatusPill({ status }) {
  const map = {
    sent:    { bg: "#DBEAFE", color: "#1E40AF", label: "Испратено" },
    waiting: { bg: "#FEF9C3", color: "#854D0E", label: "Чека" },
    replied: { bg: "#DCFCE7", color: "#166534", label: "Одговорил" },
  };
  const s = map[status] || map.waiting;
  return (
    <span style={{ background: s.bg, color: s.color, padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 600 }}>
      {s.label}
    </span>
  );
}

export default function App() {
  const [leads, setLeads] = useState([]);
  const [stats, setStats] = useState({ total: 0, sent: 0, opened: 0, replied: 0 });
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [tab, setTab] = useState("dashboard");
  const [research, setResearch] = useState([]);
  const [resLoading, setResLoading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState("");
  const [uploadedCount, setUploadedCount] = useState(0);

  useEffect(() => {
    fetchLeads();
    fetchStats();
  }, []);

  const fetchLeads = async () => {
    const res = await fetch("http://127.0.0.1:8000/leads");
    const data = await res.json();
    setLeads(data);
  };

  const fetchStats = async () => {
    const res = await fetch("http://127.0.0.1:8000/stats");
    const data = await res.json();
    setStats(data);
  };

  const handleUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("http://127.0.0.1:8000/upload-csv", {
      method: "POST",
      body: formData
    });
    const data = await res.json();
    setUploadedCount(data.count);
    setUploadMsg(`✅ Вчитани ${data.count} лидови од "${file.name}"`);
  };

  const runAgent = async () => {
    setLoading(true);
    setMessage("Агентот работи...");
    const res = await fetch("http://127.0.0.1:8000/run", { method: "POST" });
    const data = await res.json();
    setMessage(data.message);
    await fetchLeads();
    await fetchStats();
    setLoading(false);
  };

  const runResearch = async () => {
    setResLoading(true);
    const res = await fetch("http://127.0.0.1:8000/research", { method: "POST" });
    const data = await res.json();
    setResearch(data.results);
    setResLoading(false);
  };

  const filtered = leads.filter(l =>
    (l.name || "").toLowerCase().includes(search.toLowerCase()) ||
    (l.company || "").toLowerCase().includes(search.toLowerCase())
  );

  const statCards = [
    { icon: "👥", val: stats.total, lbl: "Вкупно лидови", bg: "linear-gradient(135deg,#2563EB,#3B82F6)" },
    { icon: "📧", val: stats.sent,  lbl: "Испратени",     bg: "linear-gradient(135deg,#16A34A,#22C55E)" },
    { icon: "👁️", val: stats.opened, lbl: "Отворени",     bg: "linear-gradient(135deg,#D97706,#F59E0B)" },
    { icon: "💬", val: stats.replied, lbl: "Одговори",    bg: "linear-gradient(135deg,#DC2626,#F87171)" },
  ];

  return (
    <div style={{ background: "#EFF6FF", minHeight: "100vh", fontFamily: "Inter, sans-serif", padding: "2rem" }}>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 700, color: "#1E3A5F", margin: 0 }}>📤 B2B Аутрич Dashboard</h1>
          <p style={{ fontSize: "13px", color: "#64748B", margin: "4px 0 0" }}>Скопје, Македонија — Мај 2026</p>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          <button onClick={() => setTab("dashboard")} style={{ background: tab === "dashboard" ? "#2563EB" : "white", color: tab === "dashboard" ? "white" : "#2563EB", border: "1.5px solid #2563EB", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}>
            Dashboard
          </button>
          <button onClick={() => setTab("research")} style={{ background: tab === "research" ? "#7C3AED" : "white", color: tab === "research" ? "white" : "#7C3AED", border: "1.5px solid #7C3AED", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}>
            🔬 Истражување
          </button>
          {message && <span style={{ fontSize: "12px", color: "#2563EB", fontWeight: 500 }}>{message}</span>}
          <span style={{ background: "#DCFCE7", color: "#166534", fontSize: "12px", padding: "6px 16px", borderRadius: "999px", fontWeight: 600 }}>● Активно</span>
          <button onClick={runAgent} disabled={loading} style={{ background: loading ? "#93C5FD" : "#2563EB", color: "white", border: "none", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: loading ? "not-allowed" : "pointer" }}>
            {loading ? "⏳ Работи..." : "▶ Стартувај агент"}
          </button>
        </div>
      </div>

      {/* CSV Upload Banner */}
      <div style={{ background: "white", borderRadius: "12px", padding: "1rem 1.4rem", marginBottom: "1.5rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", display: "flex", alignItems: "center", gap: "16px" }}>
        <div style={{ fontSize: "24px" }}>📂</div>
        <div style={{ flex: 1 }}>
          <p style={{ fontSize: "13px", fontWeight: 600, color: "#1E3A5F", margin: 0 }}>Качи листа на лидови</p>
          <p style={{ fontSize: "12px", color: "#64748B", margin: "2px 0 0" }}>
            CSV формат: <code style={{ background: "#EFF6FF", padding: "1px 6px", borderRadius: "4px", fontSize: "11px" }}>name, company, industry, country, email</code>
          </p>
        </div>
        {uploadMsg && (
          <span style={{ fontSize: "12px", color: "#0F766E", fontWeight: 600, background: "#CCFBF1", padding: "4px 12px", borderRadius: "999px" }}>
            {uploadMsg}
          </span>
        )}
        <label style={{ background: "#0F766E", color: "white", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer", whiteSpace: "nowrap" }}>
          📂 Избери CSV
          <input type="file" accept=".csv" onChange={handleUpload} style={{ display: "none" }} />
        </label>
      </div>

      {/* Dashboard Tab */}
      {tab === "dashboard" && (
        <>
          {/* Stat Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px", marginBottom: "1.5rem" }}>
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

          {/* Bar Chart */}
          <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)", marginBottom: "1.5rem" }}>
            <p style={{ fontSize: "11px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: "1.2rem" }}>Лидови по индустрија</p>
            {industries.map((ind, i) => (
              <div key={i} style={{ marginBottom: "14px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "5px" }}>
                  <span style={{ fontSize: "13px", color: "#334155", fontWeight: 500 }}>{ind.label}</span>
                  <span style={{ fontSize: "12px", color: "#94A3B8" }}>{ind.count} лидови</span>
                </div>
                <div style={{ height: "8px", background: "#EFF6FF", borderRadius: "4px", overflow: "hidden" }}>
                  <div style={{ width: `${ind.pct}%`, height: "100%", background: ind.color, borderRadius: "4px" }} />
                </div>
              </div>
            ))}
          </div>

          {/* Leads Table */}
          <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1rem" }}>
              <p style={{ fontSize: "11px", fontWeight: 700, color: "#94A3B8", textTransform: "uppercase", letterSpacing: "0.08em", margin: 0 }}>Лидови</p>
              <span style={{ fontSize: "12px", color: "#64748B" }}>{filtered.length} резултати</span>
            </div>
            <input
              type="text"
              placeholder="🔍  Пребарај по ime или компанија..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ width: "100%", padding: "10px 16px", fontSize: "13px", border: "1.5px solid #BFDBFE", borderRadius: "10px", marginBottom: "1rem", background: "#F8FAFF", color: "#1E293B", outline: "none" }}
            />
            {leads.length === 0 ? (
              <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8" }}>
                <div style={{ fontSize: "40px", marginBottom: "1rem" }}>📭</div>
                <p style={{ fontSize: "14px" }}>Нема лидови. Качи CSV и кликни "Стартувај агент".</p>
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ background: "#F1F5FF" }}>
                    {["Контакт", "Компанија", "Индустрија", "Email", "Статус", "Отворено"].map(h => (
                      <th key={h} style={{ padding: "10px 14px", textAlign: "left", fontSize: "11px", color: "#64748B", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((l, i) => (
                    <tr key={i} style={{ borderTop: "1px solid #F1F5FF", background: i % 2 === 0 ? "white" : "#FAFCFF" }}>
                      <td style={{ padding: "12px 14px" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                          <div style={{ width: "32px", height: "32px", borderRadius: "50%", background: "linear-gradient(135deg,#2563EB,#60A5FA)", color: "white", fontSize: "11px", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                            {(l.name || "?").split(" ").map(w => w[0]).join("").slice(0, 2)}
                          </div>
                          <span style={{ fontSize: "13px", color: "#1E293B", fontWeight: 500 }}>{l.name}</span>
                        </div>
                      </td>
                      <td style={{ padding: "12px 14px", fontSize: "13px", color: "#334155" }}>{l.company}</td>
                      <td style={{ padding: "12px 14px", fontSize: "13px", color: "#334155" }}>{l.industry}</td>
                      <td style={{ padding: "12px 14px", fontSize: "12px", color: "#2563EB", fontWeight: 500 }}>{l.email}</td>
                      <td style={{ padding: "12px 14px" }}><StatusPill status={l.status} /></td>
                      <td style={{ padding: "12px 14px", fontSize: "18px" }}>{l.opened ? "✅" : "❌"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {/* Research Tab */}
      {tab === "research" && (
        <div style={{ background: "white", borderRadius: "14px", padding: "1.4rem", boxShadow: "0 2px 12px rgba(37,99,235,0.07)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
            <h2 style={{ fontSize: "16px", fontWeight: 700, color: "#1E3A5F", margin: 0 }}>🔬 Споредба на Prompt Техники</h2>
            <button onClick={runResearch} disabled={resLoading} style={{ background: resLoading ? "#C4B5FD" : "#7C3AED", color: "white", border: "none", borderRadius: "8px", padding: "8px 18px", fontSize: "13px", fontWeight: 600, cursor: "pointer" }}>
              {resLoading ? "⏳ Генерира..." : "▶ Стартувај истражување"}
            </button>
          </div>

          {research.length === 0 ? (
            <div style={{ textAlign: "center", padding: "3rem", color: "#94A3B8" }}>
              <div style={{ fontSize: "40px", marginBottom: "1rem" }}>🔬</div>
              <p>Кликни "Стартувај истражување" за да ги споредиш 3-те prompt техники.</p>
            </div>
          ) : (
            research.map((r, i) => (
              <div key={i} style={{ marginBottom: "2rem", borderBottom: "1px solid #EFF6FF", paddingBottom: "2rem" }}>
                <h3 style={{ fontSize: "15px", fontWeight: 700, color: "#1E3A5F", marginBottom: "1rem" }}>
                  {r.lead} — {r.company}
                </h3>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "12px" }}>
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

    </div>
  );
}