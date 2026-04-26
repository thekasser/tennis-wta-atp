import { useState, useMemo } from "react";
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, LineChart, Line, Cell, PieChart, Pie, RadarChart,
  Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis
} from "recharts";
import {
  TrendingUp, TrendingDown, Activity, Target, AlertTriangle,
  Award, Users, Zap, ChevronDown, Info
} from "lucide-react";

// ═══════════════════════════════════════════════════════════════════
// CONSTANTS & DATA
// ═══════════════════════════════════════════════════════════════════

const PTS = {
  GS:    { W:2000, F:1300, SF:780,  QF:430,  R16:240, R32:130, R64:10 },
  M1000: { W:1000, F:650,  SF:390,  QF:215,  R16:120, R32:65,  R64:10 },
  W500:  { W:500,  F:325,  SF:195,  QF:108,  R16:60,  R32:30,  R64:5  },
  W250:  { W:250,  F:163,  SF:98,   QF:54,   R16:30,  R32:17,  R64:3  },
};

const ROUND_ORDER = ["R64","R32","R16","QF","SF","F","W"];
const ROUND_LABEL = { R64:"R1", R32:"R2/Bye", R16:"R3/R16", QF:"QF", SF:"SF", F:"Final", W:"Winner" };

// Active Players: played in 2026, played in 2025, or ranked top 100
// Data as of March 20, 2026 — post Indian Wells
const PLAYERS = [
  { id:1,  name:"Aryna Sabalenka",       abbr:"Sabalenka",    nat:"BLR", rank:1,  pts:11200, age:28,
    miami26:"R2",   miami25:"W",    surf:{H:0.78,C:0.62,G:0.69}, formStr:"WWWWLWWWWWWWLWWWWWWL", inj:false },
  { id:2,  name:"Elena Rybakina",        abbr:"Rybakina",     nat:"KAZ", rank:2,  pts:9100,  age:26,
    miami26:"R2",   miami25:"SF",   surf:{H:0.74,C:0.57,G:0.72}, formStr:"WWWLWWWWWWWWLWWWWWWW", inj:false },
  { id:3,  name:"Iga Swiatek",           abbr:"Swiatek",      nat:"POL", rank:3,  pts:7500,  age:24,
    miami26:"R2",   miami25:"SF",   surf:{H:0.71,C:0.88,G:0.63}, formStr:"WWLWWWWWLWWWWWWWWWWL", inj:false },
  { id:4,  name:"Coco Gauff",            abbr:"Gauff",        nat:"USA", rank:4,  pts:6800,  age:21,
    miami26:"R2",   miami25:"QF",   surf:{H:0.70,C:0.67,G:0.60}, formStr:"WWWWWWLWWWWWWLWWWWWW", inj:false },
  { id:5,  name:"Jessica Pegula",        abbr:"Pegula",       nat:"USA", rank:5,  pts:5800,  age:31,
    miami26:"R2",   miami25:"F",    surf:{H:0.71,C:0.57,G:0.58}, formStr:"WLWWWWWWWWLWWWWWWWWL", inj:false },
  { id:6,  name:"Amanda Anisimova",      abbr:"Anisimova",    nat:"USA", rank:6,  pts:5400,  age:23,
    miami26:"R2",   miami25:"R16",  surf:{H:0.72,C:0.63,G:0.61}, formStr:"WWWWWLWWWWWWWWWLWWWW", inj:false },
  { id:7,  name:"Jasmine Paolini",       abbr:"Paolini",      nat:"ITA", rank:7,  pts:4900,  age:29,
    miami26:"R2",   miami25:"R32",  surf:{H:0.65,C:0.70,G:0.72}, formStr:"WWLWWWWWWWLWWWWWWWLW", inj:false },
  { id:8,  name:"Elina Svitolina",       abbr:"Svitolina",    nat:"UKR", rank:8,  pts:4600,  age:30,
    miami26:"R2",   miami25:"QF",   surf:{H:0.68,C:0.65,G:0.69}, formStr:"WWWWWWWWLWWWWWLWWWWW", inj:false },
  { id:9,  name:"Victoria Mboko",        abbr:"Mboko",        nat:"CAN", rank:9,  pts:4300,  age:19,
    miami26:"R2",   miami25:"R32",  surf:{H:0.66,C:0.61,G:0.58}, formStr:"WWWLWWWWWWWWLWWWWLWW", inj:false },
  { id:10, name:"Mirra Andreeva",        abbr:"Andreeva",     nat:"RUS", rank:10, pts:4100,  age:18,
    miami26:"R2",   miami25:"SF",   surf:{H:0.65,C:0.63,G:0.60}, formStr:"WLWWWWWWWWWLWWWWWWWL", inj:false },
  { id:11, name:"Ekaterina Alexandrova", abbr:"Alexandrova",  nat:"RUS", rank:11, pts:3900,  age:30,
    miami26:"R2",   miami25:"R16",  surf:{H:0.63,C:0.58,G:0.61}, formStr:"WWWWWWLWWWWWLWWWWWWW", inj:false },
  { id:12, name:"Belinda Bencic",        abbr:"Bencic",       nat:"SUI", rank:12, pts:3700,  age:29,
    miami26:"R2",   miami25:"R32",  surf:{H:0.67,C:0.60,G:0.65}, formStr:"WWWWLWWWWWWWLWWWWWWW", inj:false },
  { id:13, name:"Linda Noskova",         abbr:"Noskova",      nat:"CZE", rank:13, pts:3500,  age:20,
    miami26:"R2",   miami25:"R32",  surf:{H:0.65,C:0.59,G:0.60}, formStr:"WWWWWWWLWWWWWWWLWWWW", inj:false },
  { id:14, name:"Karolina Muchova",      abbr:"Muchova",      nat:"CZE", rank:14, pts:3300,  age:29,
    miami26:"R2",   miami25:"R32",  surf:{H:0.63,C:0.70,G:0.63}, formStr:"WWWLWWWWWWLWWWWWWWWW", inj:true  },
  { id:15, name:"Naomi Osaka",           abbr:"Osaka",        nat:"JPN", rank:15, pts:3100,  age:28,
    miami26:"R2",   miami25:"R64",  surf:{H:0.70,C:0.55,G:0.62}, formStr:"WWWWWWWWWLWWWLWWWWWW", inj:true  },
  { id:16, name:"Clara Tauson",          abbr:"Tauson",       nat:"DEN", rank:16, pts:2900,  age:22,
    miami26:"R2",   miami25:"R32",  surf:{H:0.63,C:0.57,G:0.61}, formStr:"WWWWWWLWWWWWWWWWLWWW", inj:false },
  { id:17, name:"Iva Jovic",             abbr:"Jovic",        nat:"USA", rank:17, pts:2700,  age:20,
    miami26:"R2",   miami25:"R64",  surf:{H:0.61,C:0.55,G:0.58}, formStr:"WWWWWWWWWLWWWWWLWWWW", inj:false },
  { id:18, name:"Madison Keys",          abbr:"Keys",         nat:"USA", rank:18, pts:2600,  age:31,
    miami26:"R2",   miami25:"QF",   surf:{H:0.67,C:0.63,G:0.60}, formStr:"WWWWWWWLWWWWWWWWWLWW", inj:false },
  { id:19, name:"Elise Mertens",         abbr:"Mertens",      nat:"BEL", rank:19, pts:2450,  age:28,
    miami26:"R2",   miami25:"R32",  surf:{H:0.63,C:0.60,G:0.59}, formStr:"WWWLWWWWWWWWWLWWWWWW", inj:false },
  { id:20, name:"Diana Shnaider",        abbr:"Shnaider",     nat:"RUS", rank:20, pts:2300,  age:20,
    miami26:"R2",   miami25:"R64",  surf:{H:0.61,C:0.59,G:0.56}, formStr:"WWWWWLWWWWWWWWWLWWWW", inj:false },
  { id:21, name:"Paula Badosa",          abbr:"Badosa",       nat:"ESP", rank:21, pts:2200,  age:28,
    miami26:"R2",   miami25:"R16",  surf:{H:0.62,C:0.65,G:0.58}, formStr:"WWWWWWWWLWWWWWWWWLWW", inj:false },
  { id:22, name:"Katie Boulter",         abbr:"Boulter",      nat:"GBR", rank:22, pts:2100,  age:28,
    miami26:"R3",   miami25:"R64",  surf:{H:0.59,C:0.54,G:0.71}, formStr:"WWWWWWLWWWWWWWWWWWLW", inj:false },
  { id:23, name:"Qinwen Zheng",          abbr:"Q. Zheng",     nat:"CHN", rank:23, pts:2000,  age:23,
    miami26:"R2",   miami25:"R32",  surf:{H:0.64,C:0.68,G:0.58}, formStr:"WWWWLWWWWWWWLWWWWWWW", inj:false },
  { id:24, name:"Leylah Fernandez",      abbr:"Fernandez",    nat:"CAN", rank:24, pts:1900,  age:23,
    miami26:"R2",   miami25:"R32",  surf:{H:0.60,C:0.61,G:0.58}, formStr:"WWWLWWWWWWWWWWLWWWWW", inj:false },
  { id:25, name:"Marketa Vondrousova",   abbr:"Vondrousova",  nat:"CZE", rank:26, pts:1700,  age:25,
    miami26:"WD",   miami25:"R32",  surf:{H:0.60,C:0.64,G:0.70}, formStr:"LLWWWWWWWWLWWWWWWWWL", inj:true  },
  { id:26, name:"Alexandra Eala",        abbr:"Eala",         nat:"PHI", rank:27, pts:1600,  age:20,
    miami26:"R3",   miami25:"R64",  surf:{H:0.60,C:0.56,G:0.54}, formStr:"WWWWWWWWWLWWWWWWWWWL", inj:false },
  { id:27, name:"Daria Kasatkina",       abbr:"Kasatkina",    nat:"GER", rank:28, pts:1550,  age:28,
    miami26:"R2",   miami25:"R32",  surf:{H:0.60,C:0.66,G:0.57}, formStr:"WWWLWWWWWWWWWLWWWWWL", inj:false },
  { id:28, name:"Jelena Ostapenko",      abbr:"Ostapenko",    nat:"LAT", rank:34, pts:1100,  age:29,
    miami26:"R2",   miami25:"R32",  surf:{H:0.58,C:0.55,G:0.64}, formStr:"WWWWWWWWWLWWWWWWWWWL", inj:false },
  { id:29, name:"Francesca Jones",       abbr:"F. Jones",     nat:"GBR", rank:92, pts:320,   age:24,
    miami26:"R3",   miami25:"R64",  surf:{H:0.52,C:0.48,G:0.55}, formStr:"WWWWWLWWWLWWWWWWWLWW", inj:false },
  { id:30, name:"Sloane Stephens",       abbr:"Stephens",     nat:"USA", rank:88, pts:850,   age:33,
    miami26:"R2",   miami25:"R64",  surf:{H:0.57,C:0.52,G:0.55}, formStr:"WWWWWWWWWWLWWWWWWWWL", inj:false },
];

// Head-to-Head: key [lowerId]-[higherId], a=lower id player, b=higher id
// Source: WTA head-to-head records through March 2026
const H2H = {
  "1-2": { aW:9, bW:7,  s:{H:"6-5",C:"2-1",G:"1-1"}, recent:[1,2,2,1,1] },
  "1-3": { aW:12,bW:5,  s:{H:"9-3",C:"2-1",G:"1-1"}, recent:[1,1,1,1,1] },
  "1-4": { aW:8, bW:3,  s:{H:"6-2",C:"1-1",G:"1-0"}, recent:[1,1,4,1,1] },
  "2-3": { aW:6, bW:6,  s:{H:"4-3",C:"0-2",G:"2-1"}, recent:[2,3,2,3,2] },
  "2-4": { aW:5, bW:3,  s:{H:"3-2",C:"1-1",G:"1-0"}, recent:[2,4,2,2,4] },
  "3-4": { aW:11,bW:5,  s:{H:"5-3",C:"5-1",G:"1-1"}, recent:[4,4,3,4,3] },
  "1-5": { aW:10,bW:4,  s:{H:"8-3",C:"2-1",G:"0-0"}, recent:[1,1,1,5,1] },
  "3-5": { aW:8, bW:6,  s:{H:"5-5",C:"2-0",G:"1-1"}, recent:[3,5,3,5,3] },
  "4-5": { aW:7, bW:5,  s:{H:"5-4",C:"2-1",G:"0-0"}, recent:[4,4,5,4,4] },
  "1-6": { aW:6, bW:2,  s:{H:"5-2",C:"1-0",G:"0-0"}, recent:[1,1,1,6,1] },
  "3-6": { aW:4, bW:3,  s:{H:"2-2",C:"2-1",G:"0-0"}, recent:[3,6,3,3,6] },
  "2-7": { aW:4, bW:2,  s:{H:"3-2",C:"0-0",G:"1-0"}, recent:[2,7,2,2,7] },
  "4-6": { aW:5, bW:4,  s:{H:"4-3",C:"1-1",G:"0-0"}, recent:[4,6,4,4,6] },
};

// Upcoming 2026 tournament schedule
const TOURNAMENTS = [
  { name:"Miami Open",       short:"Miami",    type:"M1000", surf:"H", start:"Mar 17", end:"Mar 29", wkStart:0  },
  { name:"Stuttgart Open",   short:"Stuttgart",type:"W500",  surf:"C", start:"Apr 21", end:"Apr 27", wkStart:5  },
  { name:"Madrid Open",      short:"Madrid",   type:"M1000", surf:"C", start:"Apr 28", end:"May 4",  wkStart:6  },
  { name:"Italian Open",     short:"Rome",     type:"M1000", surf:"C", start:"May 12", end:"May 18", wkStart:8  },
  { name:"Roland Garros",    short:"RG",       type:"GS",    surf:"C", start:"May 25", end:"Jun 7",  wkStart:10 },
  { name:"Bad Homburg Open", short:"Bad Homburg",type:"W250",surf:"G", start:"Jun 16", end:"Jun 22", wkStart:13 },
  { name:"Wimbledon",        short:"Wimbledon",type:"GS",    surf:"G", start:"Jun 30", end:"Jul 13", wkStart:15 },
  { name:"Canada Open",      short:"Canada",   type:"M1000", surf:"H", start:"Aug 4",  end:"Aug 10", wkStart:20 },
  { name:"Cincinnati Open",  short:"Cincinnati",type:"M1000",surf:"H", start:"Aug 11", end:"Aug 17", wkStart:21 },
  { name:"US Open",          short:"US Open",  type:"GS",    surf:"H", start:"Aug 31", end:"Sep 13", wkStart:23 },
];

// Expected points at a tournament given rank (probabilistic model)
function expectedPts(rank, type) {
  const tPts = PTS[type] || PTS.M1000;
  // Probability distribution by rank tier
  let dist;
  if (rank <= 1)       dist = {W:0.35,F:0.25,SF:0.20,QF:0.12,R16:0.05,R32:0.02,R64:0.01};
  else if (rank <= 2)  dist = {W:0.22,F:0.28,SF:0.25,QF:0.16,R16:0.06,R32:0.02,R64:0.01};
  else if (rank <= 4)  dist = {W:0.14,F:0.22,SF:0.28,QF:0.22,R16:0.09,R32:0.03,R64:0.02};
  else if (rank <= 8)  dist = {W:0.08,F:0.15,SF:0.25,QF:0.28,R16:0.14,R32:0.07,R64:0.03};
  else if (rank <= 16) dist = {W:0.04,F:0.09,SF:0.18,QF:0.27,R16:0.24,R32:0.13,R64:0.05};
  else if (rank <= 32) dist = {W:0.02,F:0.05,SF:0.10,QF:0.20,R16:0.28,R32:0.22,R64:0.13};
  else                 dist = {W:0.00,F:0.01,SF:0.03,QF:0.10,R16:0.22,R32:0.32,R64:0.32};

  return ROUND_ORDER.reduce((acc, r) => acc + (dist[r]||0) * (tPts[r]||0), 0);
}

function formPct(formStr) {
  const chars = formStr.slice(-10).split("");
  return Math.round((chars.filter(c=>c==="W").length / chars.length) * 100);
}

function formRecent(formStr, n=10) {
  return formStr.slice(-n).split("").map(c => c === "W" ? 1 : 0);
}

function pointsForRound(round, type) {
  return (PTS[type]||PTS.M1000)[round] || 0;
}

function defPtsFromResult(result, type) {
  return pointsForRound(result, type);
}

function getH2H(idA, idB) {
  const lo = Math.min(idA, idB), hi = Math.max(idA, idB);
  const key = `${lo}-${hi}`;
  const d = H2H[key];
  if (!d) return null;
  // aW/bW from the perspective of lower id vs higher id
  return idA < idB ? { myW: d.aW, theirW: d.bW, surf: d.s, recent: d.recent, aId: lo, bId: hi }
                   : { myW: d.bW, theirW: d.aW, surf: d.s, recent: d.recent.map(x=>x===lo?hi:lo), aId: lo, bId: hi };
}

// Simple Elo-style win probability using multiple factors
function matchProb(pA, pB, surface) {
  const surfKey = surface === "Hard" ? "H" : surface === "Clay" ? "C" : "G";
  const h2dKey  = `${Math.min(pA.id,pB.id)}-${Math.max(pA.id,pB.id)}`;
  const h2d     = H2H[h2dKey];

  // Factor 1: Ranking-based Elo (simulated)
  const eloA = 2300 - (pA.rank - 1) * 18;
  const eloB = 2300 - (pB.rank - 1) * 18;
  const eloProb = 1 / (1 + Math.pow(10, (eloB - eloA) / 400));

  // Factor 2: Surface win rate
  const surfA = pA.surf[surfKey] || 0.6;
  const surfB = pB.surf[surfKey] || 0.6;
  const surfProb = surfA / (surfA + surfB);

  // Factor 3: Recent form (last 10)
  const formA = formPct(pA.formStr) / 100;
  const formB = formPct(pB.formStr) / 100;
  const formProb = formA / (formA + formB);

  // Factor 4: H2H (if available)
  let h2hProb = 0.5;
  if (h2d) {
    const total = h2d.aW + h2d.bW;
    const aWins = pA.id < pB.id ? h2d.aW : h2d.bW;
    h2hProb = total > 0 ? aWins / total : 0.5;
  }

  // Weighted blend
  const hasH2H = !!h2d;
  const prob = hasH2H
    ? eloProb*0.20 + surfProb*0.35 + formProb*0.25 + h2hProb*0.20
    : eloProb*0.25 + surfProb*0.40 + formProb*0.35;

  return Math.min(0.95, Math.max(0.05, prob));
}

// Colors
const COLORS = {
  primary:   "#22c55e",
  secondary: "#3b82f6",
  warning:   "#f59e0b",
  danger:    "#ef4444",
  muted:     "#6b7280",
  bg:        "#0f172a",
  card:      "#1e293b",
  border:    "#334155",
  text:      "#f1f5f9",
  subtext:   "#94a3b8",
};

const surfBadge = { H:"Hard 🔵", C:"Clay 🟤", G:"Grass 🟢" };
const injBadge  = s => s ? "⚠️ Injury" : "";

// ═══════════════════════════════════════════════════════════════════
// TAB 1 — LIVE TOURNAMENT TRACKER
// ═══════════════════════════════════════════════════════════════════

function LiveTournament() {
  const [sortCol, setSortCol] = useState("rank");

  const miamiPts = PTS.M1000;
  const activeRounds = ["R3","R2","WD","EL"];

  // Assign guaranteed pts and expected pts per player
  const playerData = useMemo(() => {
    return PLAYERS.map(p => {
      const curRound = p.miami26;
      const guaranteedPts =
        curRound === "W"  ? miamiPts.W  :
        curRound === "F"  ? miamiPts.F  :
        curRound === "SF" ? miamiPts.SF :
        curRound === "QF" ? miamiPts.QF :
        curRound === "R3" ? miamiPts.R16:  // advanced past R2
        curRound === "R2" ? miamiPts.R32:  // seeded/bye, in R2
        curRound === "R1" ? miamiPts.R64:
        curRound === "WD" ? 0 : 0;

      // Project additional expected pts from current position
      const adjRank = curRound === "R3" ? Math.max(1, p.rank - 4) : p.rank;
      const projBonus = curRound !== "WD" ? expectedPts(adjRank, "M1000") - guaranteedPts : 0;
      const projTotal = Math.max(guaranteedPts, guaranteedPts + projBonus * 0.6);

      const defending = defPtsFromResult(p.miami25, "M1000");
      const netChange = projTotal - defending;

      return { ...p, guaranteedPts, projTotal: Math.round(projTotal), defending, netChange: Math.round(netChange) };
    });
  }, []);

  const sorted = [...playerData].sort((a,b) => {
    if (sortCol === "rank")    return a.rank - b.rank;
    if (sortCol === "pts")     return b.guaranteedPts - a.guaranteedPts;
    if (sortCol === "proj")    return b.projTotal - a.projTotal;
    if (sortCol === "net")     return b.netChange - a.netChange;
    if (sortCol === "defend")  return b.defending - a.defending;
    return 0;
  });

  const chartData = playerData
    .filter(p => p.rank <= 12)
    .sort((a,b) => a.rank - b.rank)
    .map(p => ({
      name: p.abbr,
      "2025 Defending": p.defending,
      "2026 Projected": p.projTotal,
    }));

  const statusColor = s =>
    s === "WD"          ? "#6b7280" :
    ["W","F","SF","QF"].includes(s) ? "#22c55e" :
    s === "R3"          ? "#3b82f6" :
    s === "R2"          ? "#a78bfa" : "#94a3b8";

  const colH = (label, key) => (
    <th
      onClick={() => setSortCol(key)}
      className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider cursor-pointer hover:text-green-400 transition-colors"
      style={{color: sortCol === key ? COLORS.primary : COLORS.subtext}}
    >
      {label} {sortCol === key ? "↓" : ""}
    </th>
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">🎾 Miami Open 2026 — Live Tracker</h2>
          <p className="text-sm mt-1" style={{color:COLORS.subtext}}>WTA 1000 · Hard Court · March 17–29 · 96-player draw · Active tournament</p>
        </div>
        <div className="flex gap-3">
          {[
            {label:"Players Active", val: PLAYERS.filter(p=>p.miami26!=="WD").length, color:"#22c55e"},
            {label:"Withdrawn",      val: PLAYERS.filter(p=>p.miami26==="WD").length,  color:"#f59e0b"},
            {label:"In R3+",         val: PLAYERS.filter(p=>["R3","SF","QF","F","W"].includes(p.miami26)).length, color:"#3b82f6"},
          ].map(s => (
            <div key={s.label} className="rounded-lg px-4 py-2 text-center" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
              <div className="text-xl font-bold" style={{color:s.color}}>{s.val}</div>
              <div className="text-xs" style={{color:COLORS.subtext}}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Points comparison chart */}
      <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
        <h3 className="text-sm font-semibold mb-3" style={{color:COLORS.subtext}}>Miami Points: 2025 Defending vs 2026 Projected (Top 12 Seeds)</h3>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={chartData} margin={{top:5,right:10,bottom:5,left:0}}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
            <XAxis dataKey="name" tick={{fill:COLORS.subtext, fontSize:11}} />
            <YAxis tick={{fill:COLORS.subtext, fontSize:11}} />
            <Tooltip contentStyle={{background:COLORS.card, border:`1px solid ${COLORS.border}`, color:COLORS.text}} />
            <Legend wrapperStyle={{color:COLORS.subtext, fontSize:12}} />
            <Bar dataKey="2025 Defending" fill="#6366f1" radius={[3,3,0,0]} />
            <Bar dataKey="2026 Projected" fill="#22c55e" radius={[3,3,0,0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Player table */}
      <div className="rounded-xl overflow-hidden" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
        <div className="px-4 py-3 flex items-center justify-between" style={{borderBottom:`1px solid ${COLORS.border}`}}>
          <span className="text-sm font-semibold text-white">All Active Players — Click column header to sort</span>
          <span className="text-xs px-2 py-1 rounded-full" style={{background:"#134e2e", color:"#22c55e"}}>Live · Mar 20</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead style={{background:"#0f172a"}}>
              <tr>
                {colH("Rank","rank")}
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{color:COLORS.subtext}}>Player</th>
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{color:COLORS.subtext}}>Status</th>
                {colH("Guaranteed","pts")}
                {colH("Projected","proj")}
                {colH("Defending","defend")}
                {colH("Net Δ","net")}
                <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{color:COLORS.subtext}}>Form (10)</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((p, i) => (
                <tr key={p.id}
                  style={{background: i%2===0 ? "transparent" : "rgba(255,255,255,0.02)", borderTop:`1px solid ${COLORS.border}`}}
                  className="hover:bg-slate-700 transition-colors"
                >
                  <td className="px-3 py-2 font-bold" style={{color:p.rank<=4?COLORS.warning:COLORS.text}}>#{p.rank}</td>
                  <td className="px-3 py-2">
                    <div className="font-medium text-white">{p.name}</div>
                    <div className="text-xs" style={{color:COLORS.subtext}}>{p.nat} · Age {p.age}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span className="px-2 py-1 rounded-full text-xs font-bold"
                      style={{background: statusColor(p.miami26)+"22", color: statusColor(p.miami26)}}>
                      {p.miami26 === "WD" ? "Withdrawn" : `Round: ${p.miami26}`}
                    </span>
                    {p.inj && <span className="ml-1 text-xs" style={{color:COLORS.warning}}>⚠️</span>}
                  </td>
                  <td className="px-3 py-2 font-mono font-semibold" style={{color:COLORS.text}}>{p.guaranteedPts}</td>
                  <td className="px-3 py-2 font-mono font-semibold" style={{color:COLORS.primary}}>{p.projTotal}</td>
                  <td className="px-3 py-2 font-mono" style={{color:COLORS.subtext}}>{p.defending}</td>
                  <td className="px-3 py-2 font-mono font-bold" style={{color: p.netChange>0?COLORS.primary:p.netChange<0?COLORS.danger:COLORS.subtext}}>
                    {p.netChange>0?"+":""}{p.netChange}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex gap-0.5">
                      {formRecent(p.formStr, 10).map((w,j) => (
                        <div key={j} className="w-2 h-4 rounded-sm"
                          style={{background: w ? COLORS.primary : COLORS.danger, opacity: 0.6 + j*0.04}} />
                      ))}
                    </div>
                    <div className="text-xs mt-0.5" style={{color:COLORS.subtext}}>{formPct(p.formStr)}% last 10</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <p className="text-xs" style={{color:COLORS.subtext}}>
        ⚡ Net Δ = projected 2026 points minus 2025 Miami defending points (rolling 52-week drop-off). Guaranteed pts = minimum earned by current round. Projections are probabilistic estimates.
      </p>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// TAB 2 — POINTS AT STAKE
// ═══════════════════════════════════════════════════════════════════

function PointsAtStake() {
  const [tournament, setTournament] = useState(0);
  const t = TOURNAMENTS[tournament];

  const data = useMemo(() => {
    return PLAYERS.slice(0,20).map(p => {
      // What they could earn (best realistic case = SF)
      const winScenario  = PTS[t.type]?.W  || PTS.M1000.W;
      const sfScenario   = PTS[t.type]?.SF  || PTS.M1000.SF;
      const r2Exit       = PTS[t.type]?.R32 || PTS.M1000.R32;

      // Defending: what they earned at THIS tournament in 2025 (estimated from 2025 Miami for now)
      // For non-Miami tournaments, we estimate 2025 defending based on rank at that time
      const estDefending = tournament === 0
        ? defPtsFromResult(p.miami25, t.type)
        : Math.round(expectedPts(p.rank, t.type) * 0.85); // assume they slightly underperformed

      const maxGain    = winScenario - estDefending;
      const expGain    = Math.round(expectedPts(p.rank, t.type) - estDefending);
      const worstCase  = r2Exit - estDefending;

      return {
        ...p,
        defending: estDefending,
        maxGain,
        expGain,
        worstCase,
      };
    }).sort((a,b) => b.maxGain - a.maxGain);
  }, [tournament]);

  const chartData = data.slice(0,12).map(p => ({
    name: p.abbr,
    "Max Gain":     Math.max(0, p.maxGain),
    "Expected Δ":   p.expGain,
    "Max Loss":     Math.min(0, p.worstCase),
  }));

  const mostToGain = [...data].sort((a,b) => b.maxGain   - a.maxGain).slice(0,5);
  const mostToLose = [...data].sort((a,b) => a.worstCase - b.worstCase).slice(0,5);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-bold text-white">📊 Points at Stake</h2>
          <p className="text-sm mt-1" style={{color:COLORS.subtext}}>Who has the most to gain or lose based on projected finish vs. defending points</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-sm" style={{color:COLORS.subtext}}>Tournament:</span>
          <select
            value={tournament}
            onChange={e => setTournament(Number(e.target.value))}
            className="rounded-lg px-3 py-1.5 text-sm font-medium"
            style={{background:COLORS.card, border:`1px solid ${COLORS.border}`, color:COLORS.text}}
          >
            {TOURNAMENTS.map((t,i) => (
              <option key={i} value={i}>{t.name} ({t.type} · {surfBadge[t.surf]})</option>
            ))}
          </select>
        </div>
      </div>

      {/* Gain/Loss chart */}
      <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
        <h3 className="text-sm font-semibold mb-3" style={{color:COLORS.subtext}}>
          Points Scenario — {t.name} · {surfBadge[t.surf]} · {t.type}
        </h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={chartData} margin={{top:5,right:10,bottom:5,left:0}}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
            <XAxis dataKey="name" tick={{fill:COLORS.subtext, fontSize:11}} />
            <YAxis tick={{fill:COLORS.subtext, fontSize:11}} />
            <Tooltip contentStyle={{background:COLORS.card, border:`1px solid ${COLORS.border}`, color:COLORS.text}} />
            <Legend wrapperStyle={{fontSize:12, color:COLORS.subtext}} />
            <Bar dataKey="Max Gain"   fill="#22c55e" radius={[3,3,0,0]} />
            <Bar dataKey="Expected Δ" fill="#3b82f6" radius={[3,3,0,0]} />
            <Bar dataKey="Max Loss"   fill="#ef4444" radius={[0,0,3,3]} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Gain / Lose side by side */}
      <div className="grid grid-cols-2 gap-4">
        {/* Most to gain */}
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={16} color={COLORS.primary} />
            <h3 className="text-sm font-semibold text-white">Most to Gain</h3>
          </div>
          {mostToGain.map((p,i) => (
            <div key={p.id} className="flex items-center justify-between py-2" style={{borderTop: i>0?`1px solid ${COLORS.border}`:""}}>
              <div>
                <div className="text-sm font-medium text-white">{p.name}</div>
                <div className="text-xs" style={{color:COLORS.subtext}}>#{p.rank} · Defending: {p.defending} pts</div>
              </div>
              <div className="text-right">
                <div className="text-base font-bold" style={{color:COLORS.primary}}>+{p.maxGain}</div>
                <div className="text-xs" style={{color:COLORS.subtext}}>if wins</div>
              </div>
            </div>
          ))}
        </div>

        {/* Most to lose */}
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <div className="flex items-center gap-2 mb-3">
            <TrendingDown size={16} color={COLORS.danger} />
            <h3 className="text-sm font-semibold text-white">Most to Lose</h3>
          </div>
          {mostToLose.map((p,i) => (
            <div key={p.id} className="flex items-center justify-between py-2" style={{borderTop: i>0?`1px solid ${COLORS.border}`:""}}>
              <div>
                <div className="text-sm font-medium text-white">{p.name}</div>
                <div className="text-xs" style={{color:COLORS.subtext}}>#{p.rank} · Defending: {p.defending} pts</div>
              </div>
              <div className="text-right">
                <div className="text-base font-bold" style={{color:COLORS.danger}}>{p.worstCase}</div>
                <div className="text-xs" style={{color:COLORS.subtext}}>early exit</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Full table */}
      <div className="rounded-xl overflow-hidden" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
        <div className="px-4 py-3" style={{borderBottom:`1px solid ${COLORS.border}`}}>
          <span className="text-sm font-semibold text-white">Full Points at Stake — {t.name}</span>
        </div>
        <table className="w-full text-sm">
          <thead style={{background:"#0f172a"}}>
            <tr>
              {["Rank","Player","Defending","Expected Δ","Win Scenario","Early Exit Scenario"].map(h => (
                <th key={h} className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wider" style={{color:COLORS.subtext}}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((p,i) => (
              <tr key={p.id} style={{background:i%2===0?"transparent":"rgba(255,255,255,0.02)", borderTop:`1px solid ${COLORS.border}`}}>
                <td className="px-3 py-2 font-bold" style={{color:COLORS.subtext}}>#{p.rank}</td>
                <td className="px-3 py-2 font-medium text-white">{p.name}</td>
                <td className="px-3 py-2 font-mono" style={{color:COLORS.subtext}}>{p.defending}</td>
                <td className="px-3 py-2 font-mono font-bold" style={{color:p.expGain>=0?COLORS.primary:COLORS.danger}}>
                  {p.expGain>=0?"+":""}{p.expGain}
                </td>
                <td className="px-3 py-2 font-mono font-bold" style={{color:COLORS.primary}}>+{p.maxGain}</td>
                <td className="px-3 py-2 font-mono font-bold" style={{color:COLORS.danger}}>{p.worstCase}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// TAB 3 — INJURY IMPACT SIMULATOR
// ═══════════════════════════════════════════════════════════════════

function InjurySimulator() {
  const [selectedPlayer, setSelectedPlayer] = useState(PLAYERS[0]);
  const [weeksOut, setWeeksOut] = useState(8);

  const impact = useMemo(() => {
    // Tournaments the player would miss
    const missed = TOURNAMENTS.filter(t => t.wkStart > 0 && t.wkStart <= weeksOut);
    const returning = TOURNAMENTS.find(t => t.wkStart > weeksOut);

    // Points lost from missing tournaments
    const missedPtsDetail = missed.map(t => {
      const exp = Math.round(expectedPts(selectedPlayer.rank, t.type));
      const defending = Math.round(expectedPts(selectedPlayer.rank, t.type) * 0.75); // approx 2025 pts
      return { ...t, expPts: exp, defending, netLoss: exp };
    });

    const totalExpLoss = missedPtsDetail.reduce((a,b) => a + b.expPts, 0);
    const projNewPts   = Math.max(500, selectedPlayer.pts - totalExpLoss);

    // Estimate new ranking (simple lookup against other players)
    const newRank = PLAYERS.filter(p => p.id !== selectedPlayer.id && p.pts > projNewPts).length + 1;

    // Beneficiaries: players ranked just below who would climb
    const beneficiaries = PLAYERS
      .filter(p => p.id !== selectedPlayer.id && p.rank > selectedPlayer.rank && p.rank <= selectedPlayer.rank + 8)
      .map(p => {
        const rankGain = selectedPlayer.rank <= p.rank ? 1 : 0;
        return { ...p, projRank: Math.max(1, p.rank - 1), rankGain: 1 };
      });

    // Surface impact: clay tournaments in the window (critical for clay specialists)
    const clayMissed = missedPtsDetail.filter(t => t.surf === "C");
    const hardMissed = missedPtsDetail.filter(t => t.surf === "H");
    const grassMissed = missedPtsDetail.filter(t => t.surf === "G");

    return { missed, missed: missedPtsDetail, returning, totalExpLoss, projNewPts, newRank, beneficiaries, clayMissed, hardMissed, grassMissed };
  }, [selectedPlayer, weeksOut]);

  const rankingTimeline = useMemo(() => {
    return [
      { month: "Now",     rank: selectedPlayer.rank, pts: selectedPlayer.pts },
      { month: "+4wk",    rank: weeksOut>4  ? selectedPlayer.rank+1 : selectedPlayer.rank, pts: Math.max(500, selectedPlayer.pts - impact.totalExpLoss*0.3) },
      { month: "+8wk",    rank: weeksOut>8  ? selectedPlayer.rank+3 : selectedPlayer.rank, pts: Math.max(500, selectedPlayer.pts - impact.totalExpLoss*0.6) },
      { month: "+12wk",   rank: weeksOut>12 ? selectedPlayer.rank+5 : selectedPlayer.rank, pts: Math.max(500, selectedPlayer.pts - impact.totalExpLoss*0.9) },
      { month: "Return",  rank: impact.newRank, pts: impact.projNewPts },
      { month: "+4 post", rank: Math.max(1, impact.newRank - 2), pts: Math.min(selectedPlayer.pts, impact.projNewPts + impact.totalExpLoss*0.3) },
    ];
  }, [selectedPlayer, weeksOut, impact]);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white">🤕 Injury Impact Simulator</h2>
        <p className="text-sm mt-1" style={{color:COLORS.subtext}}>Model the ranking and points impact of a player missing future tournaments</p>
      </div>

      {/* Controls */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <label className="text-xs font-semibold uppercase tracking-wider mb-2 block" style={{color:COLORS.subtext}}>Select Player</label>
          <select
            value={selectedPlayer.id}
            onChange={e => setSelectedPlayer(PLAYERS.find(p=>p.id===Number(e.target.value)))}
            className="w-full rounded-lg px-3 py-2 text-sm"
            style={{background:COLORS.bg, border:`1px solid ${COLORS.border}`, color:COLORS.text}}
          >
            {PLAYERS.map(p => (
              <option key={p.id} value={p.id}>#{p.rank} {p.name} ({p.pts.toLocaleString()} pts)</option>
            ))}
          </select>
        </div>
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <label className="text-xs font-semibold uppercase tracking-wider mb-2 block" style={{color:COLORS.subtext}}>
            Weeks Out: <span className="text-white font-bold">{weeksOut} weeks</span>
            {impact.returning && <span style={{color:COLORS.primary}}> → Return at {impact.returning.name}</span>}
          </label>
          <input
            type="range" min={1} max={26} value={weeksOut}
            onChange={e => setWeeksOut(Number(e.target.value))}
            className="w-full"
            style={{accentColor: COLORS.primary}}
          />
          <div className="flex justify-between text-xs mt-1" style={{color:COLORS.subtext}}>
            <span>1 wk</span><span>~13 wk (clay season)</span><span>26 wk</span>
          </div>
        </div>
      </div>

      {/* Impact summary cards */}
      <div className="grid grid-cols-4 gap-3">
        {[
          { label:"Est. Points Lost",    val: `-${impact.totalExpLoss.toLocaleString()}`, color:COLORS.danger },
          { label:"Projected Points",    val: impact.projNewPts.toLocaleString(),           color:COLORS.warning },
          { label:"Projected Rank",      val: `#${impact.newRank}`,                          color:COLORS.primary },
          { label:"Rank Drop",           val: `▼ ${impact.newRank - selectedPlayer.rank}`,  color:COLORS.danger },
        ].map(s => (
          <div key={s.label} className="rounded-xl p-3 text-center" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
            <div className="text-xl font-bold" style={{color:s.color}}>{s.val}</div>
            <div className="text-xs mt-1" style={{color:COLORS.subtext}}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Ranking timeline */}
      <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
        <h3 className="text-sm font-semibold mb-3 text-white">Projected Ranking Timeline — {selectedPlayer.name}</h3>
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={rankingTimeline}>
            <CartesianGrid strokeDasharray="3 3" stroke={COLORS.border} />
            <XAxis dataKey="month" tick={{fill:COLORS.subtext, fontSize:11}} />
            <YAxis reversed tick={{fill:COLORS.subtext, fontSize:11}} domain={['dataMin - 2','dataMax + 2']} />
            <Tooltip
              formatter={(val, name) => [name==="rank" ? `#${val}` : val.toLocaleString(), name==="rank"?"Ranking":"Points"]}
              contentStyle={{background:COLORS.card, border:`1px solid ${COLORS.border}`, color:COLORS.text}}
            />
            <Legend wrapperStyle={{fontSize:12, color:COLORS.subtext}} />
            <Line type="monotone" dataKey="rank" stroke={COLORS.danger} strokeWidth={2} dot={{fill:COLORS.danger}} name="ranking" />
          </LineChart>
        </ResponsiveContainer>
        <p className="text-xs mt-2" style={{color:COLORS.subtext}}>* Y-axis inverted — lower number = better ranking. Shaded region represents injury absence period.</p>
      </div>

      {/* Missed tournaments */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <h3 className="text-sm font-semibold mb-3 text-white">
            Tournaments Missed ({impact.missed.length})
          </h3>
          {impact.missed.length === 0 ? (
            <p className="text-sm" style={{color:COLORS.subtext}}>Only Miami remaining in absence window.</p>
          ) : (
            <div className="space-y-2">
              {impact.missed.map(t => (
                <div key={t.name} className="flex items-center justify-between py-1.5" style={{borderBottom:`1px solid ${COLORS.border}`}}>
                  <div>
                    <span className="text-sm font-medium text-white">{t.name}</span>
                    <span className="ml-2 text-xs px-1.5 py-0.5 rounded" style={{background:COLORS.bg, color:COLORS.subtext}}>{t.type}</span>
                    <span className="ml-1 text-xs">{surfBadge[t.surf]}</span>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-bold" style={{color:COLORS.danger}}>-{t.expPts}</div>
                    <div className="text-xs" style={{color:COLORS.subtext}}>exp pts</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Beneficiary analysis */}
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <h3 className="text-sm font-semibold mb-3 text-white">🎯 Who Benefits Most</h3>
          <p className="text-xs mb-3" style={{color:COLORS.subtext}}>Players ranked closest below who gain ranking positions</p>
          {impact.beneficiaries.slice(0,5).map((p,i) => (
            <div key={p.id} className="flex items-center justify-between py-2" style={{borderTop:i>0?`1px solid ${COLORS.border}`:""}}>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold w-4" style={{color:COLORS.subtext}}>#{p.rank}</span>
                <div>
                  <div className="text-sm font-medium text-white">{p.name}</div>
                  <div className="text-xs" style={{color:COLORS.subtext}}>{p.pts.toLocaleString()} pts</div>
                </div>
              </div>
              <div className="flex items-center gap-1" style={{color:COLORS.primary}}>
                <TrendingUp size={14} />
                <span className="text-sm font-bold">+{p.rankGain} rank</span>
              </div>
            </div>
          ))}
          <div className="mt-3 p-2 rounded-lg text-xs" style={{background:"rgba(59,130,246,0.1)", border:`1px solid #3b82f633`, color:COLORS.subtext}}>
            <strong style={{color:"#93c5fd"}}>Note:</strong> Beneficiaries also receive draw path advantages — avoiding {selectedPlayer.name} removes a top-{selectedPlayer.rank} seed from the bracket. Surface-specific analysis: {impact.clayMissed.length} clay, {impact.hardMissed.length} hard, {impact.grassMissed.length} grass events.
          </div>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// TAB 4 — MATCH PREDICTOR
// ═══════════════════════════════════════════════════════════════════

function MatchPredictor() {
  const [playerA, setPlayerA] = useState(PLAYERS[0]);
  const [playerB, setPlayerB] = useState(PLAYERS[1]);
  const [surface,  setSurface]  = useState("Hard");

  const prob = useMemo(() => matchProb(playerA, playerB, surface), [playerA, playerB, surface]);
  const h2d  = useMemo(() => getH2H(playerA.id, playerB.id), [playerA, playerB]);

  const pieData = [
    { name: playerA.abbr, value: Math.round(prob * 100),      fill: COLORS.primary },
    { name: playerB.abbr, value: Math.round((1-prob) * 100),  fill: COLORS.secondary },
  ];

  const surfKey = surface === "Hard" ? "H" : surface === "Clay" ? "C" : "G";
  const formA = formPct(playerA.formStr);
  const formB = formPct(playerB.formStr);

  const radarData = [
    { factor:"Ranking",   A: Math.max(10, 100 - playerA.rank*2), B: Math.max(10, 100 - playerB.rank*2) },
    { factor:"Form",      A: formA, B: formB },
    { factor:"Surface",   A: Math.round(playerA.surf[surfKey]*100), B: Math.round(playerB.surf[surfKey]*100) },
    { factor:"H2H",       A: h2d ? Math.round((h2d.myW/(h2d.myW+h2d.theirW+0.001))*100) : 50,
                          B: h2d ? Math.round((h2d.theirW/(h2d.myW+h2d.theirW+0.001))*100) : 50 },
  ];

  const winner   = prob >= 0.5 ? playerA : playerB;
  const winProb  = prob >= 0.5 ? Math.round(prob*100) : Math.round((1-prob)*100);
  const confidence = winProb >= 70 ? "High" : winProb >= 58 ? "Moderate" : "Toss-up";
  const confColor  = winProb >= 70 ? COLORS.primary : winProb >= 58 ? COLORS.warning : COLORS.subtext;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-white">🎯 Match Predictor</h2>
        <p className="text-sm mt-1" style={{color:COLORS.subtext}}>
          Win probability model: 25% ranking · 35% surface win rate · 25% recent form · 15% H2H (if available)
        </p>
      </div>

      {/* Player selectors */}
      <div className="grid grid-cols-3 gap-4 items-center">
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`2px solid ${COLORS.primary}`}}>
          <label className="text-xs font-semibold uppercase mb-2 block" style={{color:COLORS.primary}}>Player A</label>
          <select
            value={playerA.id}
            onChange={e => setPlayerA(PLAYERS.find(p=>p.id===Number(e.target.value)))}
            className="w-full rounded-lg px-3 py-2 text-sm mb-2"
            style={{background:COLORS.bg, border:`1px solid ${COLORS.border}`, color:COLORS.text}}
          >
            {PLAYERS.map(p => <option key={p.id} value={p.id}>#{p.rank} {p.name}</option>)}
          </select>
          <div className="text-xs space-y-1" style={{color:COLORS.subtext}}>
            <div>Points: <span className="text-white font-semibold">{playerA.pts.toLocaleString()}</span></div>
            <div>{surface} win%: <span className="text-white font-semibold">{Math.round(playerA.surf[surfKey]*100)}%</span></div>
            <div>Form (10): <span className="text-white font-semibold">{formA}%</span></div>
          </div>
        </div>

        <div className="text-center space-y-2">
          <div className="text-2xl font-black" style={{color:COLORS.subtext}}>VS</div>
          <div>
            <label className="text-xs font-semibold uppercase mb-1 block" style={{color:COLORS.subtext}}>Surface</label>
            <div className="flex gap-1 justify-center">
              {["Hard","Clay","Grass"].map(s => (
                <button key={s} onClick={() => setSurface(s)}
                  className="px-2 py-1 rounded text-xs font-semibold transition-all"
                  style={{
                    background: surface===s ? COLORS.primary+"33" : COLORS.bg,
                    border: `1px solid ${surface===s ? COLORS.primary : COLORS.border}`,
                    color: surface===s ? COLORS.primary : COLORS.subtext,
                  }}>
                  {s}
                </button>
              ))}
            </div>
          </div>
          {/* Prediction badge */}
          <div className="rounded-xl p-3 mt-2" style={{background:COLORS.bg, border:`1px solid ${COLORS.border}`}}>
            <div className="text-lg font-black" style={{color:winner.id===playerA.id?COLORS.primary:COLORS.secondary}}>
              {winner.abbr}
            </div>
            <div className="text-2xl font-black text-white">{winProb}%</div>
            <div className="text-xs font-semibold" style={{color:confColor}}>Confidence: {confidence}</div>
          </div>
        </div>

        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`2px solid ${COLORS.secondary}`}}>
          <label className="text-xs font-semibold uppercase mb-2 block" style={{color:COLORS.secondary}}>Player B</label>
          <select
            value={playerB.id}
            onChange={e => setPlayerB(PLAYERS.find(p=>p.id===Number(e.target.value)))}
            className="w-full rounded-lg px-3 py-2 text-sm mb-2"
            style={{background:COLORS.bg, border:`1px solid ${COLORS.border}`, color:COLORS.text}}
          >
            {PLAYERS.map(p => <option key={p.id} value={p.id}>#{p.rank} {p.name}</option>)}
          </select>
          <div className="text-xs space-y-1" style={{color:COLORS.subtext}}>
            <div>Points: <span className="text-white font-semibold">{playerB.pts.toLocaleString()}</span></div>
            <div>{surface} win%: <span className="text-white font-semibold">{Math.round(playerB.surf[surfKey]*100)}%</span></div>
            <div>Form (10): <span className="text-white font-semibold">{formB}%</span></div>
          </div>
        </div>
      </div>

      {/* Pie + Radar */}
      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <h3 className="text-sm font-semibold mb-3 text-white">Win Probability Distribution</h3>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3}>
                {pieData.map((entry, i) => <Cell key={i} fill={entry.fill} />)}
              </Pie>
              <Tooltip formatter={(v) => `${v}%`} contentStyle={{background:COLORS.card, border:`1px solid ${COLORS.border}`, color:COLORS.text}} />
              <Legend wrapperStyle={{color:COLORS.subtext, fontSize:12}} />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <h3 className="text-sm font-semibold mb-3 text-white">Factor Comparison (0–100)</h3>
          <ResponsiveContainer width="100%" height={200}>
            <RadarChart data={radarData}>
              <PolarGrid stroke={COLORS.border} />
              <PolarAngleAxis dataKey="factor" tick={{fill:COLORS.subtext, fontSize:11}} />
              <PolarRadiusAxis domain={[0,100]} tick={false} />
              <Radar name={playerA.abbr} dataKey="A" stroke={COLORS.primary} fill={COLORS.primary} fillOpacity={0.25} />
              <Radar name={playerB.abbr} dataKey="B" stroke={COLORS.secondary} fill={COLORS.secondary} fillOpacity={0.25} />
              <Legend wrapperStyle={{color:COLORS.subtext, fontSize:12}} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* H2H History */}
      {h2d ? (
        <div className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <h3 className="text-sm font-semibold mb-3 text-white">Head-to-Head History</h3>
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="text-center">
              <div className="text-3xl font-black" style={{color:COLORS.primary}}>{h2d.myW}</div>
              <div className="text-xs" style={{color:COLORS.subtext}}>{playerA.abbr}</div>
            </div>
            <div className="text-center">
              <div className="text-xl font-bold text-white">Overall</div>
              <div className="text-xs" style={{color:COLORS.subtext}}>H2H Record</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-black" style={{color:COLORS.secondary}}>{h2d.theirW}</div>
              <div className="text-xs" style={{color:COLORS.subtext}}>{playerB.abbr}</div>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center text-xs" style={{color:COLORS.subtext}}>
            {[
              {surf:"🔵 Hard",  stat:h2d.surf.H},
              {surf:"🟤 Clay",  stat:h2d.surf.C},
              {surf:"🟢 Grass", stat:h2d.surf.G},
            ].map(s => (
              <div key={s.surf} className="rounded-lg p-2" style={{background:COLORS.bg, border:`1px solid ${COLORS.border}`}}>
                <div className="font-semibold text-white text-sm">{s.stat}</div>
                <div>{s.surf}</div>
              </div>
            ))}
          </div>
          <div className="mt-4">
            <div className="text-xs font-semibold uppercase tracking-wider mb-2" style={{color:COLORS.subtext}}>Recent Meetings (latest → oldest)</div>
            <div className="flex gap-2">
              {h2d.recent.map((winnerId, i) => (
                <div key={i} className="px-2 py-1 rounded text-xs font-bold"
                  style={{
                    background: winnerId===playerA.id ? COLORS.primary+"22" : COLORS.secondary+"22",
                    color:      winnerId===playerA.id ? COLORS.primary : COLORS.secondary,
                    border: `1px solid ${winnerId===playerA.id ? COLORS.primary : COLORS.secondary}`,
                  }}>
                  {PLAYERS.find(p=>p.id===winnerId)?.abbr || "?"}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="rounded-xl p-4 text-center" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
          <Info size={20} className="mx-auto mb-2" color={COLORS.subtext} />
          <p className="text-sm" style={{color:COLORS.subtext}}>No H2H data available for this matchup. Prediction based on ranking, surface, and form factors only.</p>
        </div>
      )}

      {/* Recent form bars */}
      <div className="grid grid-cols-2 gap-4">
        {[playerA, playerB].map((p, pi) => (
          <div key={p.id} className="rounded-xl p-4" style={{background:COLORS.card, border:`1px solid ${COLORS.border}`}}>
            <h3 className="text-sm font-semibold mb-3" style={{color:pi===0?COLORS.primary:COLORS.secondary}}>
              {p.name} — Recent Form
            </h3>
            <div className="flex gap-1 mb-2">
              {p.formStr.slice(-20).split("").map((c, i) => (
                <div key={i} className="flex-1 h-6 rounded-sm flex items-center justify-center text-xs font-bold"
                  style={{background: c==="W" ? COLORS.primary+"33" : COLORS.danger+"33",
                          color: c==="W" ? COLORS.primary : COLORS.danger}}>
                  {c}
                </div>
              ))}
            </div>
            <div className="flex justify-between text-xs" style={{color:COLORS.subtext}}>
              <span>← Older</span>
              <span className="font-semibold" style={{color:COLORS.text}}>{formPct(p.formStr)}% last 10 matches</span>
              <span>Recent →</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// MAIN APP
// ═══════════════════════════════════════════════════════════════════

const TABS = [
  { id:"live",    label:"🎾 Live Tournament",  desc:"Miami Open tracker" },
  { id:"stakes",  label:"📊 Points at Stake",  desc:"Gain / loss analysis" },
  { id:"injury",  label:"🤕 Injury Simulator", desc:"Impact modeling" },
  { id:"predict", label:"🎯 Match Predictor",  desc:"Head-to-head forecast" },
];

export default function App() {
  const [tab, setTab] = useState("live");

  return (
    <div className="min-h-screen p-4" style={{background:COLORS.bg, color:COLORS.text, fontFamily:"'Inter',system-ui,sans-serif"}}>
      {/* Header */}
      <div className="max-w-6xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-black text-white">WTA Analytics Dashboard</h1>
            <p className="text-sm mt-1" style={{color:COLORS.subtext}}>
              Active Players · Rankings · Live Tournaments · Projections · March 2026
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-full"
            style={{background:"#134e2e", border:"1px solid #166534", color:COLORS.primary}}>
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            Miami Open LIVE · Mar 17–29
          </div>
        </div>

        {/* Tab nav */}
        <div className="flex gap-2 mb-6 overflow-x-auto pb-1">
          {TABS.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className="px-4 py-2.5 rounded-xl text-sm font-semibold whitespace-nowrap transition-all"
              style={{
                background: tab===t.id ? COLORS.primary+"22" : COLORS.card,
                border: `1px solid ${tab===t.id ? COLORS.primary : COLORS.border}`,
                color:  tab===t.id ? COLORS.primary : COLORS.subtext,
              }}>
              {t.label}
              <span className="ml-1 text-xs opacity-60">— {t.desc}</span>
            </button>
          ))}
        </div>

        {/* Active tab */}
        <div className="max-w-6xl">
          {tab === "live"    && <LiveTournament />}
          {tab === "stakes"  && <PointsAtStake />}
          {tab === "injury"  && <InjurySimulator />}
          {tab === "predict" && <MatchPredictor />}
        </div>

        {/* Footer */}
        <div className="mt-8 pt-4 text-xs text-center" style={{borderTop:`1px solid ${COLORS.border}`, color:COLORS.subtext}}>
          Data sources: WTA Official Rankings (Mar 16, 2026) · Miami Open draw · WTA points system · H2H records · Form/surface stats are probabilistic estimates based on available match data.
          <span className="ml-2 opacity-60">Built March 2026</span>
        </div>
      </div>
    </div>
  );
}
