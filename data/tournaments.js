// tournaments.js — 2026 ATP/WTA combined calendar
// Updated: 2026-04-01
// draw: player draw size | type: GS/M1000/M500/M250/W1000/W500/W250
// pts: point structure key in PTS table
// active: true if currently in progress | complete: true if finished

const TOURNAMENTS_DATA = [
  // ── JANUARY ──────────────────────────────────────────────────────────────
  { id:"ao26",       name:"Australian Open",    short:"AO",       tour:"BOTH", type:"GS",    surf:"H", draw:128, wk:2,  month:1,  startDate:"2026-01-12", endDate:"2026-01-25", active:false, complete:true  },

  // ── FEBRUARY ─────────────────────────────────────────────────────────────
  { id:"doha26",     name:"Qatar TotalEnergies Open", short:"Doha", tour:"WTA", type:"W500",  surf:"H", draw:28,  wk:5,  month:2,  startDate:"2026-02-09", endDate:"2026-02-15", active:false, complete:true  },
  { id:"dubai_w26",  name:"Dubai Duty Free",    short:"Dubai",    tour:"WTA", type:"W1000", surf:"H", draw:64,  wk:6,  month:2,  startDate:"2026-02-16", endDate:"2026-02-22", active:false, complete:true  },
  { id:"rdam26",     name:"ABN AMRO Open",      short:"Rotterdam",tour:"ATP", type:"M500",  surf:"H", draw:32,  wk:5,  month:2,  startDate:"2026-02-02", endDate:"2026-02-08", active:false, complete:true  },
  { id:"dubai_a26",  name:"Dubai Duty Free",    short:"Dubai",    tour:"ATP", type:"M500",  surf:"H", draw:32,  wk:7,  month:2,  startDate:"2026-02-23", endDate:"2026-03-01", active:false, complete:true  },
  { id:"buenosaires26", name:"Argentina Open",  short:"Buenos Aires", tour:"ATP", type:"M250", surf:"C", draw:28, wk:6, month:2, startDate:"2026-02-09", endDate:"2026-02-15", active:false, complete:true  },

  // ── MARCH ─────────────────────────────────────────────────────────────────
  { id:"iw26",       name:"Indian Wells",       short:"IW",       tour:"BOTH", type:"M1000", surf:"H", draw:96,  wk:9,  month:3,  startDate:"2026-03-04", endDate:"2026-03-15", active:false, complete:true  },
  { id:"miami26",    name:"Miami Open",         short:"Miami",    tour:"BOTH", type:"M1000", surf:"H", draw:128, wk:11, month:3,  startDate:"2026-03-17", endDate:"2026-03-30", active:false, complete:true  },

  // ── APRIL (current) ────────────────────────────────────────────────────────
  { id:"charleston26", name:"Credit One Charleston", short:"Charleston", tour:"WTA", type:"W500", surf:"C", draw:48, wk:13, month:4, startDate:"2026-03-30", endDate:"2026-04-06", active:false, complete:true, wtaId:804, wtaSlug:"charleston" },
  { id:"houston26",  name:"Houston Open",       short:"Houston",  tour:"ATP", type:"M250",  surf:"C", draw:28,  wk:13, month:4,  startDate:"2026-03-30", endDate:"2026-04-06", active:false, complete:true  },
  { id:"marrakech26",name:"Grand Prix Hassan II",short:"Marrakech",tour:"ATP", type:"M250",  surf:"C", draw:28,  wk:13, month:4,  startDate:"2026-03-30", endDate:"2026-04-06", active:false, complete:true  },

  { id:"madrid26",   name:"Mutua Madrid Open",  short:"Madrid",   tour:"BOTH", type:"M1000", surf:"C", draw:96,  wk:16, month:4,  startDate:"2026-04-22", endDate:"2026-05-03", active:true,  complete:false, wtaId:"madrid-open", wtaSlug:"", apiId:{atp:21325, wta:16721} },

  // ── MAY ───────────────────────────────────────────────────────────────────
  { id:"rome26",     name:"Internazionali BNL d'Italia", short:"Rome", tour:"BOTH", type:"M1000", surf:"C", draw:96, wk:18, month:5, startDate:"2026-05-06", endDate:"2026-05-17", active:false, complete:false },
  { id:"rg26",       name:"Roland Garros",      short:"RG",       tour:"BOTH", type:"GS",    surf:"C", draw:128, wk:21, month:5,  startDate:"2026-05-24", endDate:"2026-06-07", active:false, complete:false },

  // ── JUNE ──────────────────────────────────────────────────────────────────
  { id:"eastbourne26", name:"Eastbourne",       short:"Eastbourne",tour:"WTA", type:"W500",  surf:"G", draw:28,  wk:24, month:6,  startDate:"2026-06-14", endDate:"2026-06-20", active:false, complete:false },
  { id:"queens26",   name:"cinch Championships",short:"Queen's",  tour:"ATP", type:"M500",  surf:"G", draw:32,  wk:24, month:6,  startDate:"2026-06-15", endDate:"2026-06-21", active:false, complete:false },
  { id:"halle26",    name:"Terra Wortmann Open",short:"Halle",    tour:"ATP", type:"M500",  surf:"G", draw:32,  wk:24, month:6,  startDate:"2026-06-15", endDate:"2026-06-21", active:false, complete:false },
  { id:"wimbledon26",name:"Wimbledon",          short:"Wimbledon",tour:"BOTH", type:"GS",    surf:"G", draw:128, wk:26, month:6,  startDate:"2026-06-29", endDate:"2026-07-12", active:false, complete:false },

  // ── JULY ──────────────────────────────────────────────────────────────────
  { id:"hamburg26",  name:"Hamburg Open",       short:"Hamburg",  tour:"ATP", type:"M500",  surf:"C", draw:32,  wk:28, month:7,  startDate:"2026-07-13", endDate:"2026-07-19", active:false, complete:false },
  { id:"bastad26",   name:"Nordea Open",        short:"Båstad",   tour:"ATP", type:"M250",  surf:"C", draw:28,  wk:28, month:7,  startDate:"2026-07-13", endDate:"2026-07-19", active:false, complete:false },
  { id:"palermo26",  name:"Palermo Open",       short:"Palermo",  tour:"WTA", type:"W250",  surf:"C", draw:32,  wk:28, month:7,  startDate:"2026-07-13", endDate:"2026-07-19", active:false, complete:false },

  // ── AUGUST ───────────────────────────────────────────────────────────────
  { id:"montreal26", name:"National Bank Open", short:"Montreal", tour:"WTA", type:"W1000", surf:"H", draw:64,  wk:32, month:8,  startDate:"2026-08-03", endDate:"2026-08-09", active:false, complete:false },
  { id:"toronto26",  name:"National Bank Open", short:"Toronto",  tour:"ATP", type:"M1000", surf:"H", draw:96,  wk:32, month:8,  startDate:"2026-08-03", endDate:"2026-08-09", active:false, complete:false },
  { id:"cincinnati26",name:"Western & Southern Open",short:"Cincinnati",tour:"BOTH",type:"M1000",surf:"H",draw:96,wk:33,month:8, startDate:"2026-08-10", endDate:"2026-08-16", active:false, complete:false },
  { id:"uso26",      name:"US Open",            short:"US Open",  tour:"BOTH", type:"GS",    surf:"H", draw:128, wk:35, month:8,  startDate:"2026-08-24", endDate:"2026-09-06", active:false, complete:false },

  // ── SEPTEMBER / OCTOBER ───────────────────────────────────────────────────
  { id:"beijing26",  name:"China Open",         short:"Beijing",  tour:"WTA", type:"W1000", surf:"H", draw:64,  wk:40, month:9,  startDate:"2026-09-21", endDate:"2026-09-27", active:false, complete:false },
  { id:"shanghai26", name:"Rolex Shanghai Masters",short:"Shanghai",tour:"ATP",type:"M1000",surf:"H", draw:96,  wk:41, month:10, startDate:"2026-10-05", endDate:"2026-10-11", active:false, complete:false },
  { id:"wuhan26",    name:"Wuhan Open",         short:"Wuhan",    tour:"WTA", type:"W1000", surf:"H", draw:64,  wk:41, month:10, startDate:"2026-10-05", endDate:"2026-10-11", active:false, complete:false },

  // ── NOVEMBER ─────────────────────────────────────────────────────────────
  { id:"wta_finals26", name:"WTA Finals",       short:"WTA Finals",tour:"WTA",type:"WTAFinals",surf:"H",draw:8, wk:45, month:11, startDate:"2026-11-01", endDate:"2026-11-08", active:false, complete:false },
  { id:"atp_finals26", name:"Nitto ATP Finals", short:"ATP Finals",tour:"ATP",type:"ATPFinals",surf:"H",draw:8, wk:46, month:11, startDate:"2026-11-15", endDate:"2026-11-22", active:false, complete:false },
];

// Points lookup per tournament type
const PTS = {
  GS:         { W:2000, F:1300, SF:800,  QF:400, R16:200, R32:100, R64:50,  R128:10  },
  M1000_128:  { W:1000, F:650,  SF:390,  QF:215, R16:120, R32:65,  R64:35,  R128:10  },
  M1000_96:   { W:1000, F:650,  SF:390,  QF:215, R16:120, R32:65,  R64:10              },
  W1000:      { W:1000, F:650,  SF:390,  QF:215, R16:120, R32:65,  R64:10              },
  M500:       { W:500,  F:330,  SF:200,  QF:100, R16:50,  R32:20,  R64:5               },
  W500:       { W:500,  F:325,  SF:195,  QF:108, R16:60,  R32:30,  R64:5               },
  M250:       { W:250,  F:150,  SF:90,   QF:45,  R16:20,  R32:5                        },
  W250:       { W:250,  F:165,  SF:100,  QF:55,  R16:28,  R32:14                       },
  WTAFinals:  { W:1500, F:1000, SF:500,  RR:125                                        },
  ATPFinals:  { W:1500, F:1000, SF:500,  RR:200                                        },
};

// Helper: get pts for a tournament result
function getTournamentPts(tournId, round) {
  const t = TOURNAMENTS_DATA.find(x => x.id === tournId);
  if (!t) return 0;
  let ptsKey = t.type;
  // M1000 has two draw sizes
  if (t.type === 'M1000') ptsKey = t.draw === 128 ? 'M1000_128' : 'M1000_96';
  return (PTS[ptsKey] || {})[round] || 0;
}
