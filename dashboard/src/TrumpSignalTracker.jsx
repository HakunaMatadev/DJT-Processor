import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell, ScatterChart, Scatter, ZAxis
} from "recharts";

const GAMMA_API = "https://gamma-api.polymarket.com";
const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:5001";

async function fetchFromAPI(endpoint, fallback) {
  if (!API_BASE) return fallback;
  try {
    const res = await fetch(`${API_BASE}${endpoint}`);
    if (!res.ok) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

const KEYWORD_GROUPS = {
  tariffs_trade:      ["tariff","trade war","trade deal","import tax","china deal","trade deficit"],
  personnel_firing:   ["fired","resign","appoint","secretary of","you're fired","nominated"],
  iran_middle_east:   ["iran","tehran","nuclear","sanctions","israel","hamas","gaza","middle east"],
  ukraine_nato:       ["ukraine","zelensky","nato","putin","russia","kyiv","ceasefire"],
  economy_markets:    ["stock market","inflation","interest rate","federal reserve","recession","powell"],
  legal_investigation:["witch hunt","hoax","rigged","indicted","criminal","weaponized","corrupt"],
  immigration_border: ["border","illegal","deportation","deport","migrant","asylum","invasion"],
  midterms_elections: ["midterm","2026","republican majority","maga","election integrity","ballot"],
  executive_actions:  ["executive order","e.o.","proclamation","veto","signed","pardon","pardoned"],
  china:              ["china","chinese","xi jinping","beijing","ccp","tiktok","taiwan"],
  media_attacks:      ["fake news","lamestream","enemy of the people","cnn","corrupt media"],
};

const CAT_LABELS = {
  tariffs_trade:       "Tariffs & Trade",
  personnel_firing:    "Personnel / Firings",
  iran_middle_east:    "Iran & Middle East",
  ukraine_nato:        "Ukraine & NATO",
  economy_markets:     "Economy & Markets",
  legal_investigation: "Legal & Investigations",
  immigration_border:  "Immigration & Border",
  midterms_elections:  "Midterms & Elections",
  executive_actions:   "Executive Actions",
  china:               "China",
  media_attacks:       "Media Attacks",
};

const CAT_COLORS = {
  tariffs_trade:       "#378ADD",
  personnel_firing:    "#D85A30",
  iran_middle_east:    "#E24B4A",
  ukraine_nato:        "#534AB7",
  economy_markets:     "#1D9E75",
  legal_investigation: "#BA7517",
  immigration_border:  "#D4537E",
  midterms_elections:  "#639922",
  executive_actions:   "#7F77DD",
  china:               "#D85A30",
  media_attacks:       "#888780",
};

const CATEGORY_KEYWORDS_MAP = {
  tariff: "tariffs_trade", trade: "tariffs_trade",
  iran: "iran_middle_east", israel: "iran_middle_east", hamas: "iran_middle_east",
  ukraine: "ukraine_nato", nato: "ukraine_nato", russia: "ukraine_nato",
  fired: "personnel_firing", resign: "personnel_firing", appoint: "personnel_firing",
  economy: "economy_markets", inflation: "economy_markets", recession: "economy_markets",
  border: "immigration_border", migrant: "immigration_border", deport: "immigration_border",
  impeach: "legal_investigation", pardon: "executive_actions",
  midterm: "midterms_elections", election: "midterms_elections",
  china: "china", tiktok: "china", taiwan: "china",
};

function inferCategory(question) {
  const q = question.toLowerCase();
  for (const [kw, cat] of Object.entries(CATEGORY_KEYWORDS_MAP)) {
    if (q.includes(kw)) return cat;
  }
  return null;
}

function stripHtml(html) {
  if (!html) return "";
  return html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
}

function capsRatio(text) {
  const alpha = text.split("").filter(c => /[a-zA-Z]/.test(c));
  if (!alpha.length) return 0;
  return alpha.filter(c => c === c.toUpperCase()).length / alpha.length;
}

function weekKey(dateStr) {
  const d = new Date(dateStr);
  const day = d.getDay();
  const diff = d.getDate() - day + (day === 0 ? -6 : 1);
  const monday = new Date(d.setDate(diff));
  return monday.toISOString().slice(0, 10);
}

function analyzePostsLocal(posts) {
  const weeklyHits = {};
  const recentWindow = new Date(Date.now() - 14 * 86400000);
  const baselineStart = new Date(Date.now() - 44 * 86400000);

  const recentCounts = {};
  const baselineCounts = {};
  const totalByCategory = {};
  const dailyPosts = {};
  const hourHeatmap = {};
  const agitationByWeek = {};

  for (const post of posts) {
    const text = stripHtml(post.content || "").toLowerCase();
    const date = new Date(post.created_at);
    const wk = weekKey(post.created_at);
    const dateKey = date.toISOString().slice(0, 10);
    const hour = date.getHours();
    const dow = (date.getDay() + 6) % 7;

    if (!weeklyHits[wk]) weeklyHits[wk] = {};
    if (!dailyPosts[dateKey]) dailyPosts[dateKey] = { count: 0, caps: 0, excl: 0 };
    dailyPosts[dateKey].count++;
    dailyPosts[dateKey].caps += capsRatio(text);
    dailyPosts[dateKey].excl += (text.match(/!/g) || []).length;

    const hKey = `${dow}-${hour}`;
    hourHeatmap[hKey] = (hourHeatmap[hKey] || 0) + 1;

    const caps = capsRatio(stripHtml(post.content || ""));
    const excl = (text.match(/!/g) || []).length;
    if (!agitationByWeek[wk]) agitationByWeek[wk] = { caps: 0, excl: 0, n: 0 };
    agitationByWeek[wk].caps += caps;
    agitationByWeek[wk].excl += excl;
    agitationByWeek[wk].n++;

    for (const [cat, keywords] of Object.entries(KEYWORD_GROUPS)) {
      const matched = keywords.some(kw => text.includes(kw.toLowerCase()));
      if (!matched) continue;

      totalByCategory[cat] = (totalByCategory[cat] || 0) + 1;
      if (!weeklyHits[wk][cat]) weeklyHits[wk][cat] = 0;
      weeklyHits[wk][cat]++;

      if (date >= recentWindow) {
        recentCounts[cat] = (recentCounts[cat] || 0) + 1;
      } else if (date >= baselineStart) {
        baselineCounts[cat] = (baselineCounts[cat] || 0) + 1;
      }
    }
  }

  const spikes = Object.keys(KEYWORD_GROUPS).map(cat => {
    const recent = recentCounts[cat] || 0;
    const base = baselineCounts[cat] || 0;
    const dailyBase = base / 30;
    const dailyRecent = recent / 14;
    const ratio = dailyRecent / Math.max(dailyBase, 0.01);
    return { category: cat, spike: parseFloat(ratio.toFixed(2)), recent, base, total: totalByCategory[cat] || 0 };
  }).sort((a, b) => b.spike - a.spike);

  const weeks = Object.keys(weeklyHits).sort();
  const velocityChart = weeks.map(wk => {
    const row = { week: wk };
    for (const cat of Object.keys(KEYWORD_GROUPS)) {
      row[cat] = weeklyHits[wk][cat] || 0;
    }
    return row;
  });

  const heatmapData = [];
  for (let dow = 0; dow < 7; dow++) {
    for (let hour = 0; hour < 24; hour++) {
      const val = hourHeatmap[`${dow}-${hour}`] || 0;
      heatmapData.push({ dow, hour, value: val });
    }
  }

  const agitationArr = Object.keys(agitationByWeek).sort().map(wk => {
    const d = agitationByWeek[wk];
    return {
      week: wk,
      avgCaps: parseFloat(((d.caps / d.n) * 100).toFixed(1)),
      avgExcl: parseFloat((d.excl / d.n).toFixed(2)),
      posts: d.n,
    };
  });

  return { spikes, velocityChart, heatmapData, agitationArr, totalByCategory };
}

const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function HeatCell({ value, max }) {
  const intensity = max > 0 ? value / max : 0;
  const alpha = 0.08 + intensity * 0.82;
  return (
    <div style={{
      width: 20, height: 20, borderRadius: 3, flexShrink: 0,
      background: `rgba(55, 138, 221, ${alpha.toFixed(2)})`,
      title: value
    }} title={`${value} posts`} />
  );
}

function SpikeBar({ spike }) {
  const pct = Math.min(spike.spike / 5, 1) * 100;
  const color = spike.spike >= 3 ? "#E24B4A" : spike.spike >= 2 ? "#EF9F27" : spike.spike >= 1.5 ? "#63B3ED" : "#B4B2A9";
  const label = spike.spike >= 3 ? "HIGH" : spike.spike >= 2 ? "ELEVATED" : spike.spike >= 1.5 ? "WATCH" : "normal";
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 3 }}>
        <span style={{ fontSize: 13, color: "var(--color-text-primary)", fontWeight: 500 }}>
          {CAT_LABELS[spike.category] || spike.category}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color, fontWeight: 500, letterSpacing: "0.04em" }}>{label}</span>
          <span style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-primary)" }}>
            {spike.spike.toFixed(1)}×
          </span>
        </div>
      </div>
      <div style={{ height: 5, background: "var(--color-background-secondary)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 3, transition: "width 0.4s" }} />
      </div>
      <div style={{ fontSize: 11, color: "var(--color-text-secondary)", marginTop: 2 }}>
        {spike.recent} posts in last 14d vs {spike.base} in prior 30d baseline
      </div>
    </div>
  );
}

const PM_CATEGORY_MAP = {
  "Will Trump": "executive_actions",
  "tariff": "tariffs_trade",
  "trade": "tariffs_trade",
  "Iran": "iran_middle_east",
  "Ukraine": "ukraine_nato",
  "NATO": "ukraine_nato",
  "fired": "personnel_firing",
  "resign": "personnel_firing",
  "immigration": "immigration_border",
  "border": "immigration_border",
  "midterm": "midterms_elections",
  "election": "midterms_elections",
  "China": "china",
  "impeach": "legal_investigation",
};

function useLiveAPI() {
  const [apiOnline, setApiOnline] = useState(null); // null = checking
  const [opportunities, setOpportunities] = useState([]);
  const [stats, setStats] = useState(null);
  const [loadingOpps, setLoadingOpps] = useState(false);

  const refresh = useCallback(async () => {
    if (!API_BASE) {
      setApiOnline(false);
      return;
    }
    setLoadingOpps(true);
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      if (!res.ok) throw new Error("offline");
      setApiOnline(true);
      const [opps, statsData] = await Promise.all([
        fetchFromAPI("/api/opportunities", []),
        fetchFromAPI("/api/stats", null),
      ]);
      setOpportunities(opps || []);
      setStats(statsData);
    } catch {
      setApiOnline(false);
    } finally {
      setLoadingOpps(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const iv = setInterval(refresh, 5 * 60 * 1000);
    return () => clearInterval(iv);
  }, [refresh]);

  return { apiOnline, opportunities, stats, loadingOpps, refresh };
}

function BestBetRow({ opp }) {
  const side = (opp.suggested_side || "").toUpperCase();
  const sideColor = side === "YES" ? "#1D9E75" : side === "NO" ? "#E24B4A" : "#888780";
  const edge = Number(opp.edge_pp) || 0;
  const composite = Number(opp.composite_score) || 0;
  const yesPct = opp.yes_price !== "" && opp.yes_price != null ? Math.round(Number(opp.yes_price) * 100) : null;
  const question = (opp.question || "").slice(0, 60) + ((opp.question || "").length > 60 ? "…" : "");
  const catLabel = CAT_LABELS[opp.category] || opp.category || "";

  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 6,
      padding: "10px 0", borderBottom: "0.5px solid var(--color-border-tertiary)"
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 10,
          background: sideColor + "20", color: sideColor, flexShrink: 0
        }}>
          {side || "—"}
        </span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--color-text-primary)" }}>
          {question}
        </span>
        {opp.polymarket_url ? (
          <a href={opp.polymarket_url} target="_blank" rel="noreferrer"
             style={{
               fontSize: 12, fontWeight: 500, padding: "4px 10px", borderRadius: 6,
               background: "var(--color-background-secondary)", color: "var(--color-text-primary)",
               textDecoration: "none", flexShrink: 0, whiteSpace: "nowrap"
             }}>
            Bet →
          </a>
        ) : null}
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, fontSize: 12, color: "var(--color-text-secondary)" }}>
        <span>YES {yesPct !== null ? `${yesPct}%` : "—"}</span>
        <span style={{ color: edge > 0 ? "#1D9E75" : edge < 0 ? "#E24B4A" : "var(--color-text-secondary)", fontWeight: 500 }}>
          {edge > 0 ? "+" : ""}{edge.toFixed(1)}pp edge
        </span>
        <span>{catLabel} {opp.spike_ratio != null ? `· ${Number(opp.spike_ratio).toFixed(1)}×` : ""}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 90 }}>
          <div style={{ width: 60, height: 5, background: "var(--color-background-secondary)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${Math.min(composite, 1) * 100}%`, background: "#378ADD", borderRadius: 3 }} />
          </div>
          <span>{composite.toFixed(2)}</span>
        </div>
      </div>

      {opp.reason ? (
        <div style={{ fontSize: 11, color: "var(--color-text-tertiary)" }}>
          {opp.reason}
        </div>
      ) : null}
    </div>
  );
}

const SUGGESTED_TERMS = ["tariff", "China", "Iran", "fired", "pardon", "NATO", "election", "UFC", "cat", "windmill"];

const PROBABILITY_WINDOWS = [1, 3, 7, 14, 30, 60, 90];

const RATE_SOURCE_LABELS = {
  "7d": "7-day",
  "30d": "30-day",
  "90d": "90-day",
  all_time: "all-time",
};

function ProbabilityBar({ days, probability }) {
  const pct = Math.min(Math.max(probability, 0), 1) * 100;
  const color = probability >= 0.66 ? "#1D9E75" : probability >= 0.33 ? "#EF9F27" : "#E24B4A";
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
        <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
          Within {days} day{days === 1 ? "" : "s"}
        </span>
        <span style={{ fontSize: 13, fontWeight: 500, color: "var(--color-text-primary)" }}>
          {pct.toFixed(1)}%
        </span>
      </div>
      <div style={{ height: 5, background: "var(--color-background-secondary)", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${pct}%`, background: color, borderRadius: 3, transition: "width 0.4s" }} />
      </div>
    </div>
  );
}

function KeywordMarketRow({ m }) {
  const yesPct = m.yes_price != null ? Math.round(m.yes_price * 100) : null;
  const ourPct = m.our_probability != null ? Math.round(m.our_probability * 100) : null;
  const edge = m.edge;
  const edgeColor = edge > 0 ? "#1D9E75" : edge < 0 ? "#E24B4A" : "var(--color-text-secondary)";

  return (
    <div style={{ padding: "10px 0", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--color-text-primary)" }}>
          {m.question}
        </span>
        {m.polymarket_url ? (
          <a href={m.polymarket_url} target="_blank" rel="noreferrer"
             style={{
               fontSize: 12, fontWeight: 500, padding: "4px 10px", borderRadius: 6,
               background: "var(--color-background-secondary)", color: "var(--color-text-primary)",
               textDecoration: "none", flexShrink: 0, whiteSpace: "nowrap"
             }}>
            Bet →
          </a>
        ) : null}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, fontSize: 12, color: "var(--color-text-secondary)", marginTop: 4 }}>
        <span>Market YES: {yesPct !== null ? `${yesPct}%` : "—"}</span>
        <span>Our estimate: {ourPct !== null ? `${ourPct}%` : "—"}</span>
        {edge != null && (
          <span style={{ color: edgeColor, fontWeight: 500 }}>
            {edge > 0 ? "+" : ""}{(edge * 100).toFixed(1)}pp edge
          </span>
        )}
        {m.days_to_end != null && (
          <span>ends in {m.days_to_end}d ({(m.end_date || "").slice(0, 10)})</span>
        )}
      </div>
    </div>
  );
}

function KeywordOddsPanel({ apiOnline }) {
  const [inputValue, setInputValue] = useState("");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [trackDays, setTrackDays] = useState(30);
  const [tracking, setTracking] = useState(false);
  const [trackMessage, setTrackMessage] = useState(null);

  const search = useCallback(async (raw) => {
    const q = raw.trim();
    if (!q || !API_BASE) return;
    setLoading(true);
    setError(null);
    setTrackMessage(null);
    try {
      const res = await fetch(`${API_BASE}/api/keyword?q=${encodeURIComponent(q)}`);
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `HTTP ${res.status}`);
      setData(json);
    } catch (e) {
      setError(e.message || "Could not fetch keyword odds.");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const track = useCallback(async () => {
    if (!data || !API_BASE) return;
    setTracking(true);
    setTrackMessage(null);
    try {
      const res = await fetch(`${API_BASE}/api/keyword/track`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ term: data.term, days: trackDays }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json?.error || `HTTP ${res.status}`);
      setTrackMessage(
        `Tracking "${json.subject}" at ${(json.predicted_prob * 100).toFixed(1)}% — ` +
        `check the Track Record tab after ${json.check_after.slice(0, 10)}.`
      );
    } catch (e) {
      setTrackMessage(`Error: ${e.message}`);
    } finally {
      setTracking(false);
    }
  }, [data, trackDays]);

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
        Search any word or phrase to see how often Trump has posted it, and the modeled probability he
        says it again soon — the "will Trump say X this week/month?" type of bet. Probabilities use a
        Poisson model based on his recent posting rate, and are compared against any live matching
        Polymarket markets.
      </p>

      {!apiOnline && (
        <div style={{
          padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1rem",
          background: "var(--color-background-warning)", border: "0.5px solid var(--color-border-warning)"
        }}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-warning)" }}>
            <i className="ti ti-alert-triangle" aria-hidden="true" /> Flask API not detected — keyword
            odds need the full Python pipeline. Run <code>python scripts/07_api.py</code>.
          </p>
        </div>
      )}

      <form onSubmit={e => { e.preventDefault(); search(inputValue); }}
            style={{ display: "flex", gap: 8, marginBottom: "0.75rem" }}>
        <input
          type="text"
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          placeholder='Search a word or phrase, e.g. "tariff" or "cat"'
          style={{
            flex: 1, fontSize: 14, padding: "8px 12px", borderRadius: "var(--border-radius-md)",
            border: "0.5px solid var(--color-border-tertiary)", background: "var(--color-background-primary)",
            color: "var(--color-text-primary)"
          }}
        />
        <button type="submit" disabled={loading || !inputValue.trim() || !apiOnline}
                style={{ fontSize: 13, padding: "8px 16px", whiteSpace: "nowrap" }}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginBottom: "1.25rem" }}>
        <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }}>Try:</span>
        {SUGGESTED_TERMS.map(t => (
          <button key={t} onClick={() => { setInputValue(t); search(t); }} disabled={loading || !apiOnline}
                  style={{
                    fontSize: 12, padding: "3px 10px", borderRadius: 20,
                    background: "var(--color-background-secondary)", color: "var(--color-text-secondary)",
                    border: "0.5px solid var(--color-border-tertiary)"
                  }}>
            {t}
          </button>
        ))}
      </div>

      {error && (
        <div style={{
          padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1rem",
          background: "var(--color-background-danger)", border: "0.5px solid var(--color-border-danger)"
        }}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-danger)" }}>
            <i className="ti ti-alert-triangle" aria-hidden="true" /> {error}
          </p>
        </div>
      )}

      {data && data.total_mentions === 0 && (
        <p style={{ fontSize: 14, color: "var(--color-text-secondary)" }}>
          No mentions of "{data.term}" found in the archive.
        </p>
      )}

      {data && data.total_mentions > 0 && (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: "1rem" }}>
            {[
              { label: "Total mentions", value: data.total_mentions.toLocaleString() },
              { label: "Last mentioned", value: data.days_since_last != null ? `${data.days_since_last.toFixed(1)}d ago` : "never" },
              { label: "Mentions (30d)", value: data.windows["30d"].mentions },
              { label: "Mentions (90d)", value: data.windows["90d"].mentions },
            ].map(s => (
              <div key={s.label} style={{
                background: "var(--color-background-secondary)", borderRadius: "var(--border-radius-md)",
                padding: "0.75rem 1rem", flex: "1 1 120px"
              }}>
                <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 4 }}>{s.label}</div>
                <div style={{ fontSize: 20, fontWeight: 500, color: "var(--color-text-primary)" }}>{s.value}</div>
              </div>
            ))}
          </div>

          <p style={{ fontSize: 12, color: "var(--color-text-tertiary)", marginTop: 0, marginBottom: "1.25rem" }}>
            Modeled at {data.primary_rate.toFixed(3)} mentions/day, based on{" "}
            {RATE_SOURCE_LABELS[data.primary_source] || data.primary_source} activity
            {data.primary_source !== "30d" ? " (no recent 30-day mentions, so falling back to a longer window)" : ""}.
          </p>

          <div style={{ marginBottom: "1.5rem" }}>
            <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
              Probability "{data.term}" comes up again
            </h4>
            {PROBABILITY_WINDOWS.map(days => (
              <ProbabilityBar key={days} days={days} probability={data.probabilities[String(days)]} />
            ))}

            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, marginTop: 10 }}>
              <span style={{ fontSize: 12, color: "var(--color-text-secondary)" }}>
                Track this prediction:
              </span>
              <select value={trackDays} onChange={e => setTrackDays(Number(e.target.value))}
                      disabled={tracking || !apiOnline}
                      style={{
                        fontSize: 12, padding: "4px 8px", borderRadius: 6,
                        border: "0.5px solid var(--color-border-tertiary)",
                        background: "var(--color-background-primary)", color: "var(--color-text-primary)"
                      }}>
                {PROBABILITY_WINDOWS.map(d => (
                  <option key={d} value={d}>within {d} day{d === 1 ? "" : "s"}</option>
                ))}
              </select>
              <button onClick={track} disabled={tracking || !apiOnline}
                      style={{ fontSize: 12, padding: "4px 12px" }}>
                {tracking ? "Tracking…" : "Track"}
              </button>
            </div>
            {trackMessage && (
              <p style={{ fontSize: 12, color: "var(--color-text-secondary)", marginTop: 6, marginBottom: 0 }}>
                {trackMessage}
              </p>
            )}
          </div>

          {data.weekly_series.length > 0 && (
            <div style={{ marginBottom: "1.5rem" }}>
              <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
                Weekly mentions
              </h4>
              <div style={{ position: "relative", width: "100%", height: 180 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.weekly_series} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                    <XAxis dataKey="week" tick={{ fontSize: 10, fill: "var(--color-text-secondary)" }}
                           tickFormatter={v => v.slice(5)} />
                    <YAxis tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} allowDecimals={false} />
                    <Tooltip
                      contentStyle={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, fontSize: 12 }}
                      labelFormatter={l => `Week of ${l}`}
                    />
                    <Bar dataKey="mentions" fill="#378ADD" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          <div>
            <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
              Live "Will Trump say ..." markets
            </h4>
            {data.markets.length === 0 ? (
              <p style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
                No live Polymarket markets currently match "{data.term}".
              </p>
            ) : (
              data.markets.map((m, i) => <KeywordMarketRow key={i} m={m} />)
            )}
          </div>
        </>
      )}
    </div>
  );
}

function PredictionRow({ p }) {
  const predictedPct = Math.round(p.predicted_prob * 100);
  const marketPct = p.market_price != null ? Math.round(p.market_price * 100) : null;
  const status = p.actual_outcome === null ? "pending" : p.actual_outcome ? "YES" : "NO";
  const statusColor = status === "pending" ? "#888780" : status === "YES" ? "#1D9E75" : "#E24B4A";
  const typeLabel = p.pred_type === "keyword_odds" ? "Keyword Odds" : "Best Bet";
  const label = p.question || p.subject;
  const dateLabel = status === "pending"
    ? `check after ${(p.check_after || "").slice(0, 10)}`
    : `resolved ${(p.resolved_at || p.check_after || "").slice(0, 10)}`;

  return (
    <div style={{ padding: "10px 0", borderBottom: "0.5px solid var(--color-border-tertiary)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 10,
          background: statusColor + "20", color: statusColor, flexShrink: 0
        }}>
          {status}
        </span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--color-text-primary)" }}>
          {label}
        </span>
        {p.polymarket_url ? (
          <a href={p.polymarket_url} target="_blank" rel="noreferrer"
             style={{
               fontSize: 12, fontWeight: 500, padding: "4px 10px", borderRadius: 6,
               background: "var(--color-background-secondary)", color: "var(--color-text-primary)",
               textDecoration: "none", flexShrink: 0, whiteSpace: "nowrap"
             }}>
            View →
          </a>
        ) : null}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, fontSize: 12, color: "var(--color-text-secondary)", marginTop: 4 }}>
        <span>{typeLabel}</span>
        <span>Our estimate: {predictedPct}%</span>
        {marketPct !== null && <span>Market: {marketPct}%</span>}
        <span>logged {(p.logged_at || "").slice(0, 10)}</span>
        <span>{dateLabel}</span>
      </div>
    </div>
  );
}

function TrackRecordPanel({ apiOnline }) {
  const [predictions, setPredictions] = useState([]);
  const [calibration, setCalibration] = useState(null);
  const [loading, setLoading] = useState(false);
  const [filterType, setFilterType] = useState("");
  const [pendingOnly, setPendingOnly] = useState(false);

  const load = useCallback(async () => {
    if (!API_BASE) return;
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filterType) params.set("type", filterType);
      if (pendingOnly) params.set("pending", "true");
      const calQuery = filterType ? `?type=${filterType}` : "";
      const [predsRes, calRes] = await Promise.all([
        fetch(`${API_BASE}/api/predictions?${params.toString()}`),
        fetch(`${API_BASE}/api/predictions/calibration${calQuery}`),
      ]);
      setPredictions(await predsRes.json());
      setCalibration(await calRes.json());
    } catch {
      setPredictions([]);
      setCalibration(null);
    } finally {
      setLoading(false);
    }
  }, [filterType, pendingOnly]);

  useEffect(() => { load(); }, [load]);

  const pendingCount = predictions.filter(p => p.actual_outcome === null).length;
  const resolvedCount = predictions.length - pendingCount;

  const chartData = (calibration?.buckets || []).map(b => ({
    range: b.range,
    predicted: Math.round(b.avg_predicted * 1000) / 10,
    actual: Math.round(b.actual_rate * 1000) / 10,
    n: b.n,
  }));

  return (
    <div>
      <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
        Every Best Bet pick is automatically logged here with its modeled probability, and any Keyword Odds
        query you "Track" is logged the same way. Once a prediction's "check after" date passes, the resolver
        checks what actually happened — did the keyword get mentioned, did the market resolve YES? — and this
        tab reports how well-calibrated the model has been: a Brier score, and "when we said ~70%, did it
        happen ~70% of the time?" buckets.
      </p>

      {!apiOnline && (
        <div style={{
          padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1rem",
          background: "var(--color-background-warning)", border: "0.5px solid var(--color-border-warning)"
        }}>
          <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-warning)" }}>
            <i className="ti ti-alert-triangle" aria-hidden="true" /> Flask API not detected — the track record
            needs the full Python pipeline. Run <code>python scripts/07_api.py</code>.
          </p>
        </div>
      )}

      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10, marginBottom: "1rem" }}>
        <select value={filterType} onChange={e => setFilterType(e.target.value)}
                disabled={!apiOnline}
                style={{
                  fontSize: 12, padding: "4px 8px", borderRadius: 6,
                  border: "0.5px solid var(--color-border-tertiary)",
                  background: "var(--color-background-primary)", color: "var(--color-text-primary)"
                }}>
          <option value="">All types</option>
          <option value="keyword_odds">Keyword Odds</option>
          <option value="best_bet">Best Bets</option>
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, color: "var(--color-text-secondary)" }}>
          <input type="checkbox" checked={pendingOnly} onChange={e => setPendingOnly(e.target.checked)} disabled={!apiOnline} />
          Pending only
        </label>
        <button onClick={load} disabled={loading || !apiOnline} style={{ fontSize: 12, padding: "4px 12px" }}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: "1.25rem" }}>
        {[
          { label: "Total logged", value: predictions.length },
          { label: "Pending", value: pendingCount },
          { label: "Resolved", value: resolvedCount },
          { label: "Brier score", value: calibration?.brier_score != null ? calibration.brier_score.toFixed(4) : "—" },
        ].map(s => (
          <div key={s.label} style={{
            background: "var(--color-background-secondary)", borderRadius: "var(--border-radius-md)",
            padding: "0.75rem 1rem", flex: "1 1 120px"
          }}>
            <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 4 }}>{s.label}</div>
            <div style={{ fontSize: 20, fontWeight: 500, color: "var(--color-text-primary)" }}>{s.value}</div>
          </div>
        ))}
      </div>

      {chartData.length > 0 && (
        <div style={{ marginBottom: "1.5rem" }}>
          <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
            Calibration: predicted vs. actual
          </h4>
          <p style={{ fontSize: 12, color: "var(--color-text-tertiary)", marginTop: 0, marginBottom: 8 }}>
            Predictions are bucketed by their modeled probability. If the model is well-calibrated, the two
            bars in each bucket should be close — e.g. predictions made at ~70% should resolve YES about 70%
            of the time.
          </p>
          <div style={{ position: "relative", width: "100%", height: 220 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                <XAxis dataKey="range" tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} />
                <YAxis tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} unit="%" />
                <Tooltip
                  contentStyle={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, fontSize: 12 }}
                  formatter={(value, name) => [`${value}%`, name === "predicted" ? "Predicted" : "Actual"]}
                  labelFormatter={l => `Predicted ${l}`}
                />
                <Bar dataKey="predicted" name="Predicted" fill="#378ADD" radius={[2, 2, 0, 0]} />
                <Bar dataKey="actual" name="Actual" fill="#1D9E75" radius={[2, 2, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div>
        <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)" }}>
          Logged predictions
        </h4>
        {predictions.length === 0 ? (
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
            No predictions logged yet. Use the "Track" button on the Keyword Odds tab, or run{" "}
            <code>python scripts/08_score_predictions.py log-bestbets</code>.
          </p>
        ) : (
          predictions.map(p => <PredictionRow key={p.id} p={p} />)
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState("bestbets");
  const { apiOnline, opportunities, stats: liveStats, loadingOpps, refresh: refreshAPI } = useLiveAPI();
  const [posts, setPosts] = useState([]);
  const [pmMarkets, setPmMarkets] = useState([]);
  const [analysis, setAnalysis] = useState(null);
  const [loadingPosts, setLoadingPosts] = useState(false);
  const [loadingPM, setLoadingPM] = useState(false);
  const [postsError, setPostsError] = useState(null);
  const [pmError, setPmError] = useState(null);
  const [selectedCats, setSelectedCats] = useState(["tariffs_trade", "iran_middle_east", "ukraine_nato", "economy_markets"]);
  const [lastRefresh, setLastRefresh] = useState(null);

  const fetchPosts = useCallback(async () => {
    setLoadingPosts(true);
    setPostsError(null);
    try {
      const res = await fetch("https://ix.cnn.io/data/truth-social/truth_archive.json");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPosts(data);
      setAnalysis(analyzePostsLocal(data));
      setLastRefresh(new Date());
    } catch (e) {
      setPostsError("Could not load Truth Social archive. The CNN feed may be temporarily unavailable.");
    } finally {
      setLoadingPosts(false);
    }
  }, []);

  const fetchPolymarket = useCallback(async () => {
    setLoadingPM(true);
    setPmError(null);
    try {
      const res = await fetch(
        `${GAMMA_API}/markets?active=true&closed=false&limit=200&order=volume24hr&ascending=false`,
        { headers: { "User-Agent": "trump-tracker-research/1.0" } }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const trump = data.filter(m => {
        const q = (m.question || m.title || "").toLowerCase();
        return q.includes("trump") || q.includes("tariff") || q.includes("executive order") ||
               q.includes("white house") || q.includes("deportation") || q.includes("impeach");
      }).map(m => {
        let yesPrice = null;
        const prices = m.outcomePrices || [];
        if (prices.length) {
          try { yesPrice = parseFloat(prices[0]); } catch {}
        }
        if (yesPrice === null && m.lastTradePrice) {
          try { yesPrice = parseFloat(m.lastTradePrice); } catch {}
        }
        return {
          id: String(m.id || m.conditionId || ""),
          question: m.question || m.title || "",
          category: inferCategory(m.question || m.title || "") || "executive_actions",
          yesPrice,
          volume24h: parseFloat(m.volume24hr || m.volume24h || 0),
          totalVolume: parseFloat(m.volume || m.usdcVolume || 0),
          endDate: m.endDate ? new Date(m.endDate).toLocaleDateString() : "—",
        };
      }).sort((a, b) => b.volume24h - a.volume24h);
      setPmMarkets(trump);
    } catch (e) {
      setPmError("Polymarket API unavailable or CORS-blocked in browser. Use the Python scripts for full access.");
    } finally {
      setLoadingPM(false);
    }
  }, []);

  useEffect(() => {
    fetchPosts();
    fetchPolymarket();
    const iv = setInterval(() => { fetchPosts(); fetchPolymarket(); }, 30 * 60 * 1000);
    return () => clearInterval(iv);
  }, [fetchPosts, fetchPolymarket]);

  const tabs = [
    { id: "bestbets",  label: "Best Bets",      icon: "ti-target" },
    { id: "spikes",    label: "Spike Alerts",   icon: "ti-alert-triangle" },
    { id: "velocity",  label: "Keyword Velocity", icon: "ti-trending-up" },
    { id: "keywordodds", label: "Keyword Odds",  icon: "ti-search" },
    { id: "trackrecord", label: "Track Record",  icon: "ti-history" },
    { id: "polymarket",label: "Polymarket Odds",  icon: "ti-chart-bar" },
    { id: "heatmap",   label: "Posting Heatmap",  icon: "ti-clock" },
    { id: "agitation", label: "Agitation Index",  icon: "ti-flame" },
  ];

  const velocityWeeks = analysis ? analysis.velocityChart.slice(-26) : [];
  const heatMax = analysis ? Math.max(...analysis.heatmapData.map(d => d.value), 1) : 1;

  const correlationData = pmMarkets
    .filter(m => m.yesPrice !== null && analysis?.totalByCategory?.[m.category])
    .map(m => ({
      name: m.question.slice(0, 40),
      category: m.category,
      yesPrice: Math.round(m.yesPrice * 100),
      mentions: analysis.totalByCategory[m.category] || 0,
      volume: Math.round(m.volume24h),
    }));

  const pmByCategory = {};
  for (const m of pmMarkets) {
    if (!pmByCategory[m.category]) pmByCategory[m.category] = [];
    pmByCategory[m.category].push(m);
  }

  return (
    <div style={{ fontFamily: "var(--font-sans)", padding: "1.25rem 0" }}>
      <h2 className="sr-only">Trump Signal Tracker — Polymarket Correlation Dashboard</h2>

      <div style={{ marginBottom: "1.25rem" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 18, fontWeight: 500, color: "var(--color-text-primary)" }}>
              Trump Signal Tracker
            </h3>
            <p style={{ margin: "4px 0 0", fontSize: 13, color: "var(--color-text-secondary)" }}>
              Truth Social keyword velocity × Polymarket live odds
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span title={apiOnline ? "Flask API connected — full pipeline data" : "Flask API not detected — using browser-side analysis"}
                  style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--color-text-tertiary)" }}>
              <span style={{
                width: 8, height: 8, borderRadius: "50%", display: "inline-block",
                background: apiOnline ? "#1D9E75" : "#E24B4A",
              }} />
              {apiOnline ? "API connected" : "Browser-only mode"}
            </span>
            {lastRefresh && (
              <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }}>
                Updated {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={() => { fetchPosts(); fetchPolymarket(); refreshAPI(); }}
              style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 5 }}
              disabled={loadingPosts || loadingPM}
            >
              <i className="ti ti-refresh" aria-hidden="true"
                 style={{ fontSize: 15, animation: (loadingPosts || loadingPM) ? "spin 1s linear infinite" : "none" }} />
              Refresh
            </button>
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 10, marginTop: "1rem" }}>
          {(apiOnline && liveStats ? [
            { label: "Posts in DB", value: liveStats.posts_total.toLocaleString(), loading: false },
            { label: "PM snapshots", value: liveStats.pm_snapshots.toLocaleString(), loading: false },
            { label: "Resolved outcomes", value: liveStats.resolved_outcomes.toLocaleString(), loading: false },
            { label: "Best bets", value: opportunities.length, loading: loadingOpps },
          ] : [
            { label: "Posts loaded", value: posts.length.toLocaleString(), loading: loadingPosts },
            { label: "PM markets", value: pmMarkets.length, loading: loadingPM },
            { label: "Active spikes", value: analysis ? analysis.spikes.filter(s => s.spike >= 2).length : "—", loading: loadingPosts },
            { label: "Highest spike", value: analysis ? `${analysis.spikes[0]?.spike.toFixed(1)}×` : "—", loading: loadingPosts },
          ]).map(stat => (
            <div key={stat.label} style={{
              background: "var(--color-background-secondary)",
              borderRadius: "var(--border-radius-md)",
              padding: "0.75rem 1rem"
            }}>
              <div style={{ fontSize: 12, color: "var(--color-text-secondary)", marginBottom: 4 }}>{stat.label}</div>
              <div style={{ fontSize: 22, fontWeight: 500, color: "var(--color-text-primary)" }}>
                {stat.loading ? <span style={{ opacity: 0.4 }}>…</span> : stat.value}
              </div>
            </div>
          ))}
        </div>
      </div>

      {(postsError || pmError) && (
        <div style={{
          padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1rem",
          background: "var(--color-background-danger)", border: "0.5px solid var(--color-border-danger)"
        }}>
          {postsError && <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-danger)" }}><i className="ti ti-alert-triangle" aria-hidden /> {postsError}</p>}
          {pmError && <p style={{ margin: postsError ? "6px 0 0" : 0, fontSize: 13, color: "var(--color-text-danger)" }}><i className="ti ti-alert-triangle" aria-hidden /> {pmError}</p>}
        </div>
      )}

      <div style={{ display: "flex", gap: 0, borderBottom: "0.5px solid var(--color-border-tertiary)", marginBottom: "1.25rem", overflowX: "auto" }}>
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            background: "none", border: "none", padding: "0.6rem 1rem",
            fontSize: 13, fontWeight: tab === t.id ? 500 : 400, cursor: "pointer",
            color: tab === t.id ? "var(--color-text-primary)" : "var(--color-text-secondary)",
            borderBottom: tab === t.id ? "2px solid var(--color-text-primary)" : "2px solid transparent",
            whiteSpace: "nowrap", display: "flex", alignItems: "center", gap: 5
          }}>
            <i className={`ti ${t.icon}`} aria-hidden="true" style={{ fontSize: 15 }} />
            {t.label}
          </button>
        ))}
      </div>

      {tab === "bestbets" && (
        <div>
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
            Ranked picks from the Python pipeline: keyword spike strength, market edge vs. model-implied probability,
            liquidity, urgency, and mention streaks combined into a composite score.
          </p>
          {!apiOnline && (
            <div style={{
              padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1rem",
              background: "var(--color-background-warning)", border: "0.5px solid var(--color-border-warning)"
            }}>
              <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-warning)" }}>
                <i className="ti ti-alert-triangle" aria-hidden="true" /> Flask API not detected — Best Bets need the
                full Python pipeline. Run <code>python scripts/07_api.py</code> and set{" "}
                <code>REACT_APP_API_URL=http://localhost:5001</code> to see ranked picks here.
              </p>
            </div>
          )}
          {loadingOpps ? (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>Loading opportunities…</div>
          ) : opportunities.length === 0 ? (
            apiOnline ? (
              <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>
                No actionable opportunities right now. Run <code>python scripts/03_analyze.py</code> to refresh
                <code>market_opportunities.csv</code>.
              </div>
            ) : null
          ) : (
            opportunities.map((opp, i) => <BestBetRow key={opp.market_id || i} opp={opp} />)
          )}
        </div>
      )}

      {tab === "spikes" && (
        <div>
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
            Spike ratio = (posts/day in last 14d) ÷ (posts/day in prior 30d baseline). Values above 2× suggest elevated activity and potential Polymarket edge.
          </p>
          {analysis ? (
            <>
              {analysis.spikes.filter(s => s.spike >= 2).length > 0 && (
                <div style={{
                  padding: "0.75rem 1rem", borderRadius: "var(--border-radius-md)", marginBottom: "1.25rem",
                  background: "var(--color-background-warning)", border: "0.5px solid var(--color-border-warning)"
                }}>
                  <p style={{ margin: 0, fontSize: 13, fontWeight: 500, color: "var(--color-text-warning)" }}>
                    <i className="ti ti-alert-triangle" aria-hidden="true" /> {analysis.spikes.filter(s => s.spike >= 2).length} categories currently elevated — check Polymarket for edge
                  </p>
                </div>
              )}
              {analysis.spikes.map(s => <SpikeBar key={s.category} spike={s} />)}
            </>
          ) : (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>Loading post data…</div>
          )}
        </div>
      )}

      {tab === "velocity" && (
        <div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: "1rem" }}>
            {Object.keys(KEYWORD_GROUPS).map(cat => (
              <button key={cat} onClick={() => {
                setSelectedCats(prev =>
                  prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]
                );
              }} style={{
                fontSize: 12, padding: "4px 10px", borderRadius: 20,
                background: selectedCats.includes(cat) ? CAT_COLORS[cat] + "20" : "var(--color-background-secondary)",
                color: selectedCats.includes(cat) ? CAT_COLORS[cat] : "var(--color-text-secondary)",
                border: `0.5px solid ${selectedCats.includes(cat) ? CAT_COLORS[cat] : "var(--color-border-tertiary)"}`,
              }}>
                {CAT_LABELS[cat]}
              </button>
            ))}
          </div>
          {analysis && velocityWeeks.length > 0 ? (
            <div style={{ position: "relative", width: "100%", height: 280 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={velocityWeeks} margin={{ top: 5, right: 5, bottom: 5, left: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                  <XAxis dataKey="week" tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }}
                         tickFormatter={v => v.slice(5)} />
                  <YAxis tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} />
                  <Tooltip
                    contentStyle={{
                      background: "var(--color-background-primary)",
                      border: "0.5px solid var(--color-border-secondary)",
                      borderRadius: 8, fontSize: 12
                    }}
                    formatter={(val, name) => [val, CAT_LABELS[name] || name]}
                    labelFormatter={l => `Week of ${l}`}
                  />
                  {selectedCats.map(cat => (
                    <Line key={cat} type="monotone" dataKey={cat}
                          stroke={CAT_COLORS[cat]} strokeWidth={2}
                          dot={false} name={cat} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>
              {loadingPosts ? "Loading…" : "No velocity data yet."}
            </div>
          )}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: "0.75rem" }}>
            {selectedCats.map(cat => (
              <span key={cat} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--color-text-secondary)" }}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: CAT_COLORS[cat], display: "inline-block" }} />
                {CAT_LABELS[cat]}
              </span>
            ))}
          </div>
        </div>
      )}

      {tab === "keywordodds" && <KeywordOddsPanel apiOnline={apiOnline} />}

      {tab === "trackrecord" && <TrackRecordPanel apiOnline={apiOnline} />}

      {tab === "polymarket" && (
        <div>
          {loadingPM ? (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>Fetching live odds…</div>
          ) : pmMarkets.length === 0 ? (
            <div>
              <p style={{ fontSize: 13, color: "var(--color-text-secondary)" }}>
                No markets loaded. The Polymarket API may be CORS-restricted from the browser.
                Run <code>python 02_polymarket.py</code> locally for full access.
              </p>
            </div>
          ) : (
            <>
              {correlationData.length > 0 && (
                <div style={{ marginBottom: "1.5rem" }}>
                  <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: 8 }}>
                    Scatter: each bubble = one Polymarket market. X = historical post mentions of that category, Y = current YES probability. Larger bubble = higher 24h volume.
                  </p>
                  <div style={{ position: "relative", width: "100%", height: 260 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <ScatterChart margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                        <XAxis dataKey="mentions" name="Historical mentions" type="number"
                               tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }}
                               label={{ value: "Category mentions", position: "insideBottom", offset: -5, fontSize: 11, fill: "var(--color-text-secondary)" }} />
                        <YAxis dataKey="yesPrice" name="YES price %" type="number" domain={[0, 100]}
                               tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }}
                               tickFormatter={v => `${v}%`} />
                        <ZAxis dataKey="volume" range={[40, 400]} />
                        <Tooltip
                          contentStyle={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, fontSize: 12 }}
                          formatter={(val, name) => [
                            name === "YES price %" ? `${val}%` :
                            name === "Historical mentions" ? val :
                            `$${val.toLocaleString()}`, name
                          ]}
                          content={({ payload }) => {
                            if (!payload?.length) return null;
                            const d = payload[0]?.payload;
                            if (!d) return null;
                            return (
                              <div style={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, padding: "8px 12px", fontSize: 12, maxWidth: 240 }}>
                                <p style={{ margin: "0 0 4px", fontWeight: 500, color: "var(--color-text-primary)" }}>{d.name}…</p>
                                <p style={{ margin: 0, color: "var(--color-text-secondary)" }}>YES: {d.yesPrice}% · Mentions: {d.mentions}</p>
                              </div>
                            );
                          }}
                        />
                        <Scatter data={correlationData} fill="#378ADD"
                          shape={(props) => {
                            const { cx, cy, r } = props;
                            const cat = props.payload?.category;
                            return <circle cx={cx} cy={cy} r={r} fill={CAT_COLORS[cat] || "#378ADD"} fillOpacity={0.6} stroke="none" />;
                          }}
                        />
                      </ScatterChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}

              {Object.entries(pmByCategory).filter(([, ms]) => ms.length).map(([cat, ms]) => (
                <div key={cat} style={{ marginBottom: "1.25rem" }}>
                  <h4 style={{ margin: "0 0 8px", fontSize: 14, fontWeight: 500, color: "var(--color-text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: CAT_COLORS[cat], display: "inline-block" }} />
                    {CAT_LABELS[cat] || cat}
                    {analysis?.spikes.find(s => s.category === cat)?.spike >= 2 && (
                      <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 10, background: "#EF9F2720", color: "#BA7517", fontWeight: 500 }}>
                        ↑ spike
                      </span>
                    )}
                  </h4>
                  {ms.slice(0, 5).map(m => (
                    <div key={m.id} style={{
                      display: "flex", alignItems: "center", gap: 10,
                      padding: "8px 0", borderBottom: "0.5px solid var(--color-border-tertiary)"
                    }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <p style={{ margin: 0, fontSize: 13, color: "var(--color-text-primary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                          {m.question}
                        </p>
                        <p style={{ margin: "2px 0 0", fontSize: 11, color: "var(--color-text-secondary)" }}>
                          {m.endDate} · 24h vol: ${m.volume24h.toLocaleString()}
                        </p>
                      </div>
                      <div style={{ textAlign: "right", flexShrink: 0 }}>
                        {m.yesPrice !== null ? (
                          <span style={{
                            fontSize: 15, fontWeight: 500,
                            color: m.yesPrice >= 0.7 ? "#1D9E75" : m.yesPrice <= 0.3 ? "#E24B4A" : "var(--color-text-primary)"
                          }}>
                            {Math.round(m.yesPrice * 100)}%
                          </span>
                        ) : (
                          <span style={{ fontSize: 13, color: "var(--color-text-tertiary)" }}>—</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {tab === "heatmap" && (
        <div>
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
            Posting frequency by hour (EST) and day of week. Darker = more posts. Early morning bursts often signal higher-agitation content.
          </p>
          {analysis ? (
            <div style={{ overflowX: "auto" }}>
              <div style={{ display: "flex", gap: 4, marginBottom: 4 }}>
                <div style={{ width: 36 }} />
                {Array.from({ length: 24 }, (_, h) => (
                  <div key={h} style={{ width: 20, flexShrink: 0, fontSize: 10, color: "var(--color-text-tertiary)", textAlign: "center" }}>
                    {h % 6 === 0 ? h : ""}
                  </div>
                ))}
              </div>
              {Array.from({ length: 7 }, (_, dow) => (
                <div key={dow} style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 4 }}>
                  <div style={{ width: 36, fontSize: 11, color: "var(--color-text-secondary)", flexShrink: 0 }}>
                    {DOW_LABELS[dow]}
                  </div>
                  {Array.from({ length: 24 }, (_, hour) => {
                    const cell = analysis.heatmapData.find(d => d.dow === dow && d.hour === hour);
                    return <HeatCell key={hour} value={cell?.value || 0} max={heatMax} />;
                  })}
                </div>
              ))}
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: "0.75rem" }}>
                <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>Less active</span>
                {[0.1, 0.3, 0.5, 0.7, 0.9].map(a => (
                  <div key={a} style={{ width: 20, height: 20, borderRadius: 3, background: `rgba(55, 138, 221, ${a})` }} />
                ))}
                <span style={{ fontSize: 11, color: "var(--color-text-secondary)" }}>More active</span>
              </div>
            </div>
          ) : (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>Loading…</div>
          )}
        </div>
      )}

      {tab === "agitation" && (
        <div>
          <p style={{ fontSize: 13, color: "var(--color-text-secondary)", marginTop: 0, marginBottom: "1rem" }}>
            Weekly agitation index: average ALL CAPS ratio (%) and exclamation marks per post. Spikes often precede major announcements or market moves.
          </p>
          {analysis && analysis.agitationArr.length > 0 ? (
            <>
              <div style={{ position: "relative", width: "100%", height: 220 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={analysis.agitationArr.slice(-26)} margin={{ top: 5, right: 5, bottom: 20, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                    <XAxis dataKey="week" tick={{ fontSize: 10, fill: "var(--color-text-secondary)" }}
                           tickFormatter={v => v.slice(5)} angle={-30} textAnchor="end" />
                    <YAxis tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} />
                    <Tooltip
                      contentStyle={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v, n) => [n === "avgCaps" ? `${v}%` : v, n === "avgCaps" ? "Caps ratio %" : "Avg exclamations"]}
                    />
                    <Bar dataKey="avgCaps" fill="#7F77DD" name="avgCaps" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div style={{ position: "relative", width: "100%", height: 180, marginTop: "1rem" }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={analysis.agitationArr.slice(-26)} margin={{ top: 5, right: 5, bottom: 20, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border-tertiary)" />
                    <XAxis dataKey="week" tick={{ fontSize: 10, fill: "var(--color-text-secondary)" }}
                           tickFormatter={v => v.slice(5)} angle={-30} textAnchor="end" />
                    <YAxis tick={{ fontSize: 11, fill: "var(--color-text-secondary)" }} />
                    <Tooltip
                      contentStyle={{ background: "var(--color-background-primary)", border: "0.5px solid var(--color-border-secondary)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v) => [v, "Avg exclamations/post"]}
                    />
                    <Bar dataKey="avgExcl" fill="#EF9F27" name="avgExcl" radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <div style={{ display: "flex", gap: 16, marginTop: "0.5rem" }}>
                {[{ color: "#7F77DD", label: "Caps ratio %" }, { color: "#EF9F27", label: "Avg exclamations/post" }].map(l => (
                  <span key={l.label} style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "var(--color-text-secondary)" }}>
                    <span style={{ width: 10, height: 10, borderRadius: 2, background: l.color, display: "inline-block" }} />
                    {l.label}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <div style={{ color: "var(--color-text-secondary)", fontSize: 14 }}>
              {loadingPosts ? "Loading…" : "No agitation data yet."}
            </div>
          )}
        </div>
      )}

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); border: 0; }
      `}</style>
    </div>
  );
}
