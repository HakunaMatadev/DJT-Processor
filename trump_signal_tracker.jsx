import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell, ScatterChart, Scatter, ZAxis
} from "recharts";

const GAMMA_API = "https://gamma-api.polymarket.com";

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

export default function App() {
  const [tab, setTab] = useState("spikes");
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
    { id: "spikes",    label: "Spike Alerts",   icon: "ti-alert-triangle" },
    { id: "velocity",  label: "Keyword Velocity", icon: "ti-trending-up" },
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
            {lastRefresh && (
              <span style={{ fontSize: 12, color: "var(--color-text-tertiary)" }}>
                Updated {lastRefresh.toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={() => { fetchPosts(); fetchPolymarket(); }}
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
          {[
            { label: "Posts loaded", value: posts.length.toLocaleString(), loading: loadingPosts },
            { label: "PM markets", value: pmMarkets.length, loading: loadingPM },
            { label: "Active spikes", value: analysis ? analysis.spikes.filter(s => s.spike >= 2).length : "—", loading: loadingPosts },
            { label: "Highest spike", value: analysis ? `${analysis.spikes[0]?.spike.toFixed(1)}×` : "—", loading: loadingPosts },
          ].map(stat => (
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
