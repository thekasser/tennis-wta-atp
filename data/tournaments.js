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

  // ── BACKFILL: tour-level events (~99) added by scripts/_gen_catalog.py
  // Covers 2025 + 2026 ATP/WTA tour-level events not previously in this file.
  // Exclude qualifying-only / W125 / Challenger / ITF — those stay
  // unresolved in the DB and are filtered out by materialize.py.
  // ── 2025-01 ─────────────────────────
  { id:"auckland25", name:"ASB Classic - Auckland", short:"Auckland", tour:"BOTH", type:"M250", surf:"H", draw:28, wk:1, month:1, startDate:"2025-01-01", endDate:"2025-01-11", active:false, complete:true, apiId:{atp:20314, wta:15942} },  // 45 matches
  { id:"brisbane_a25", name:"Brisbane International - Brisbane", short:"Brisbane", tour:"ATP", type:"M250", surf:"H", draw:32, wk:1, month:1, startDate:"2025-01-01", endDate:"2025-01-05", active:false, complete:true, apiId:{atp:20311} },  // 15 matches
  { id:"brisbane_w25", name:"Brisbane International - Brisbane", short:"Brisbane", tour:"WTA", type:"W500", surf:"H", draw:32, wk:1, month:1, startDate:"2025-01-01", endDate:"2025-01-05", active:false, complete:true, apiId:{wta:15941} },  // 20 matches
  { id:"hongkong_a25", name:"Hong Kong Tennis Open - Hong Kong", short:"Hong Kong", tour:"ATP", type:"M250", surf:"H", draw:28, wk:1, month:1, startDate:"2025-01-01", endDate:"2025-01-04", active:false, complete:true, apiId:{atp:20312} },  // 13 matches
  { id:"adelaide_w25", name:"Adelaide International - Adelaide", short:"Adelaide", tour:"WTA", type:"W500", surf:"H", draw:32, wk:1, month:1, startDate:"2025-01-04", endDate:"2025-01-11", active:false, complete:true, apiId:{wta:15944} },  // 45 matches
  { id:"hobart25", name:"Hobart International - Hobart", short:"Hobart", tour:"WTA", type:"W250", surf:"H", draw:32, wk:1, month:1, startDate:"2025-01-04", endDate:"2025-01-11", active:false, complete:true, apiId:{wta:15943} },  // 40 matches
  { id:"adelaide_a25", name:"Adelaide International - Adelaide", short:"Adelaide", tour:"ATP", type:"M250", surf:"H", draw:32, wk:1, month:1, startDate:"2025-01-05", endDate:"2025-01-11", active:false, complete:true, apiId:{atp:20313} },  // 33 matches
  { id:"ao25", name:"Australian Open - Melbourne", short:"AO", tour:"BOTH", type:"GS", surf:"H", draw:128, wk:2, month:1, startDate:"2025-01-06", endDate:"2025-01-26", active:false, complete:true, apiId:{atp:20315, wta:15945} },  // 299 matches
  { id:"linz25", name:"Upper Austria Ladies Linz - Linz", short:"Linz", tour:"WTA", type:"W500", surf:"C", draw:32, wk:4, month:1, startDate:"2025-01-26", endDate:"2025-02-02", active:false, complete:true, apiId:{wta:15947} },  // 37 matches

  // ── 2025-02 ─────────────────────────
  { id:"abudhabi25", name:"Mubadala Abu Dhabi Open - Abu Dhabi", short:"Abu Dhabi", tour:"WTA", type:"W500", surf:"H", draw:32, wk:5, month:2, startDate:"2025-02-01", endDate:"2025-02-08", active:false, complete:true, apiId:{wta:15949} },  // 38 matches
  { id:"cluj25", name:"Transylvania Open - Cluj-Napoca", short:"Cluj", tour:"WTA", type:"W250", surf:"C", draw:32, wk:5, month:2, startDate:"2025-02-01", endDate:"2025-02-09", active:false, complete:true, apiId:{wta:15950} },  // 39 matches
  { id:"dallas25", name:"Dallas Open - Dallas", short:"Dallas", tour:"ATP", type:"M250", surf:"H", draw:28, wk:5, month:2, startDate:"2025-02-01", endDate:"2025-02-09", active:false, complete:true, apiId:{atp:20318} },  // 31 matches
  { id:"rdam25", name:"ABN AMRO Open - Rotterdam", short:"Rotterdam", tour:"ATP", type:"M500", surf:"H", draw:32, wk:5, month:2, startDate:"2025-02-01", endDate:"2025-02-09", active:false, complete:true, apiId:{atp:20319} },  // 35 matches
  { id:"doha_w25", name:"Qatar TotalEnergies Open - Doha", short:"Doha", tour:"WTA", type:"W500", surf:"H", draw:32, wk:6, month:2, startDate:"2025-02-07", endDate:"2025-02-15", active:false, complete:true, apiId:{wta:15951} },  // 76 matches
  { id:"buenosaires25", name:"Argentina Open - Buenos Aires", short:"Buenos Aires", tour:"ATP", type:"M250", surf:"C", draw:28, wk:6, month:2, startDate:"2025-02-08", endDate:"2025-02-16", active:false, complete:true, apiId:{atp:20320} },  // 25 matches
  { id:"delraybeach25", name:"Delray Beach Open - Delray Beach", short:"Delray", tour:"ATP", type:"M250", surf:"H", draw:32, wk:6, month:2, startDate:"2025-02-08", endDate:"2025-02-16", active:false, complete:true, apiId:{atp:20321} },  // 25 matches
  { id:"dubai_w25", name:"Dubai Duty Free Championships - Dubai", short:"Dubai", tour:"WTA", type:"W1000", surf:"H", draw:56, wk:7, month:2, startDate:"2025-02-14", endDate:"2025-02-22", active:false, complete:true, apiId:{wta:15952} },  // 73 matches
  { id:"doha_a25", name:"Qatar ExxonMobil Open - Doha", short:"Doha", tour:"ATP", type:"M500", surf:"H", draw:32, wk:7, month:2, startDate:"2025-02-15", endDate:"2025-02-22", active:false, complete:true, apiId:{atp:20323} },  // 40 matches
  { id:"acapulco25", name:"Abierto Mexicano Telcel - Acapulco", short:"Acapulco", tour:"ATP", type:"M500", surf:"H", draw:32, wk:8, month:2, startDate:"2025-02-22", endDate:"2025-03-01", active:false, complete:true, apiId:{atp:20325} },  // 38 matches
  { id:"dubai_a25", name:"Dubai Duty Free Tennis Championships - Dubai", short:"Dubai", tour:"ATP", type:"M500", surf:"H", draw:32, wk:8, month:2, startDate:"2025-02-22", endDate:"2025-03-01", active:false, complete:true, apiId:{atp:20326} },  // 40 matches
  { id:"merida25", name:"Merida Open Akron - Merida", short:"Mérida", tour:"WTA", type:"W250", surf:"H", draw:32, wk:8, month:2, startDate:"2025-02-22", endDate:"2025-03-02", active:false, complete:true, apiId:{wta:15996} },  // 37 matches

  // ── 2025-03 ─────────────────────────
  { id:"iw25", name:"BNP Paribas Open - Indian Wells", short:"IW", tour:"BOTH", type:"M1000", surf:"H", draw:96, wk:9, month:3, startDate:"2025-03-02", endDate:"2025-03-16", active:false, complete:true, apiId:{atp:20328, wta:15955} },  // 213 matches
  { id:"miami25", name:"Miami Open - Miami", short:"Miami", tour:"BOTH", type:"M1000", surf:"H", draw:96, wk:11, month:3, startDate:"2025-03-16", endDate:"2025-03-30", active:false, complete:true, apiId:{atp:20329, wta:15956} },  // 218 matches
  { id:"charleston25", name:"Credit One Charleston Open - Charleston", short:"Charleston", tour:"WTA", type:"W500", surf:"C", draw:48, wk:13, month:3, startDate:"2025-03-29", endDate:"2025-04-06", active:false, complete:true, apiId:{wta:15957} },  // 44 matches
  { id:"marrakech25", name:"Grand Prix Hassan II - Marrakech", short:"Marrakech", tour:"ATP", type:"M250", surf:"C", draw:28, wk:13, month:3, startDate:"2025-03-30", endDate:"2025-04-06", active:false, complete:true, apiId:{atp:20332} },  // 25 matches

  // ── 2025-04 ─────────────────────────
  { id:"montecarlo25", name:"Monte-Carlo Rolex Masters - Monte-Carlo", short:"Monte-Carlo", tour:"ATP", type:"M1000", surf:"C", draw:56, wk:14, month:4, startDate:"2025-04-05", endDate:"2025-04-13", active:false, complete:true, apiId:{atp:20333} },  // 71 matches
  { id:"barcelona25", name:"Barcelona Open Banc Sabadell - Barcelona", short:"Barcelona", tour:"ATP", type:"M500", surf:"C", draw:48, wk:15, month:4, startDate:"2025-04-12", endDate:"2025-04-20", active:false, complete:true, apiId:{atp:20334} },  // 40 matches
  { id:"munich25", name:"BMW Open - Munich", short:"Munich", tour:"ATP", type:"M250", surf:"C", draw:28, wk:15, month:4, startDate:"2025-04-12", endDate:"2025-04-20", active:false, complete:true, apiId:{atp:20335} },  // 37 matches
  { id:"stuttgart_w25", name:"Porsche Tennis Grand Prix - Stuttgart", short:"Stuttgart", tour:"WTA", type:"W500", surf:"H", draw:32, wk:15, month:4, startDate:"2025-04-12", endDate:"2025-04-21", active:false, complete:true, apiId:{wta:15959} },  // 34 matches
  { id:"madrid25", name:"Mutua Madrid Open - Madrid", short:"Madrid", tour:"BOTH", type:"M1000", surf:"C", draw:96, wk:17, month:4, startDate:"2025-04-21", endDate:"2025-05-04", active:false, complete:true, apiId:{atp:20336, wta:15961} },  // 228 matches

  // ── 2025-05 ─────────────────────────
  { id:"rome25", name:"Internazionali BNL d'Italia - Rome", short:"Rome", tour:"BOTH", type:"M1000", surf:"C", draw:96, wk:19, month:5, startDate:"2025-05-05", endDate:"2025-05-18", active:false, complete:true, apiId:{atp:20337, wta:15962} },  // 214 matches
  { id:"hamburg25", name:"Hamburg Open - Hamburg", short:"Hamburg", tour:"ATP", type:"M500", surf:"C", draw:32, wk:20, month:5, startDate:"2025-05-17", endDate:"2025-05-24", active:false, complete:true, apiId:{atp:20338} },  // 36 matches
  { id:"strasbourg25", name:"Internationaux de Strasbourg - Strasbourg", short:"Strasbourg", tour:"WTA", type:"W250", surf:"H", draw:32, wk:20, month:5, startDate:"2025-05-17", endDate:"2025-05-24", active:false, complete:true, apiId:{wta:15963} },  // 37 matches
  { id:"rg25", name:"French Open - Paris", short:"RG", tour:"BOTH", type:"GS", surf:"C", draw:128, wk:21, month:5, startDate:"2025-05-19", endDate:"2025-06-08", active:false, complete:true, apiId:{atp:20340, wta:15965} },  // 293 matches

  // ── 2025-06 ─────────────────────────
  { id:"london_w25", name:"LTA London Championships - London", short:"London", tour:"WTA", type:"W500", surf:"G", draw:32, wk:23, month:6, startDate:"2025-06-07", endDate:"2025-06-15", active:false, complete:true, apiId:{wta:15967} },  // 38 matches
  { id:"stuttgart_a25", name:"Boss Open - Stuttgart", short:"Stuttgart", tour:"ATP", type:"M250", surf:"G", draw:28, wk:23, month:6, startDate:"2025-06-07", endDate:"2025-06-15", active:false, complete:true, apiId:{atp:20343} },  // 31 matches
  { id:"berlin25", name:"Berlin Ladies Open - Berlin", short:"Berlin", tour:"WTA", type:"W500", surf:"G", draw:32, wk:24, month:6, startDate:"2025-06-14", endDate:"2025-06-22", active:false, complete:true, apiId:{wta:15969} },  // 41 matches
  { id:"halle25", name:"Terra Wortmann Open - Halle", short:"Halle", tour:"ATP", type:"M500", surf:"G", draw:32, wk:24, month:6, startDate:"2025-06-14", endDate:"2025-06-22", active:false, complete:true, apiId:{atp:20344} },  // 35 matches
  { id:"nottingham25", name:"Rothesay Open - Nottingham", short:"Nottingham", tour:"WTA", type:"W250", surf:"G", draw:32, wk:24, month:6, startDate:"2025-06-14", endDate:"2025-06-22", active:false, complete:true, apiId:{wta:15970} },  // 44 matches
  { id:"queens25", name:"cinch Championships - London", short:"Queen's", tour:"ATP", type:"M500", surf:"G", draw:32, wk:24, month:6, startDate:"2025-06-14", endDate:"2025-06-22", active:false, complete:true, apiId:{atp:20345} },  // 41 matches
  { id:"badhomburg25", name:"Bad Homburg Open - Bad Homburg", short:"Bad Homburg", tour:"WTA", type:"W500", surf:"G", draw:32, wk:25, month:6, startDate:"2025-06-21", endDate:"2025-06-28", active:false, complete:true, apiId:{wta:15971} },  // 38 matches
  { id:"eastbourne_a25", name:"Rothesay International - Eastbourne", short:"Eastbourne", tour:"ATP", type:"M250", surf:"G", draw:32, wk:25, month:6, startDate:"2025-06-21", endDate:"2025-06-28", active:false, complete:true, apiId:{atp:20347} },  // 32 matches
  { id:"eastbourne_w25", name:"Rothesay International - Eastbourne", short:"Eastbourne", tour:"WTA", type:"W500", surf:"G", draw:32, wk:25, month:6, startDate:"2025-06-21", endDate:"2025-06-28", active:false, complete:true, apiId:{wta:15972} },  // 45 matches
  { id:"mallorca25", name:"Mallorca Championships - Mallorca", short:"Mallorca", tour:"ATP", type:"M250", surf:"G", draw:28, wk:25, month:6, startDate:"2025-06-21", endDate:"2025-06-28", active:false, complete:true, apiId:{atp:20346} },  // 32 matches
  { id:"wimbledon25", name:"Wimbledon - London", short:"Wimbledon", tour:"BOTH", type:"GS", surf:"G", draw:128, wk:26, month:6, startDate:"2025-06-23", endDate:"2025-07-13", active:false, complete:true, apiId:{atp:20348, wta:15973} },  // 307 matches

  // ── 2025-07 ─────────────────────────
  { id:"bastad25", name:"Nordea Open - Bastad", short:"Båstad", tour:"ATP", type:"M250", surf:"C", draw:28, wk:28, month:7, startDate:"2025-07-13", endDate:"2025-07-20", active:false, complete:true, apiId:{atp:20350} },  // 22 matches
  { id:"washington_a25", name:"Citi Open - Washington", short:"Washington", tour:"ATP", type:"M500", surf:"H", draw:32, wk:29, month:7, startDate:"2025-07-19", endDate:"2025-07-27", active:false, complete:true, apiId:{atp:20353} },  // 49 matches
  { id:"washington_w25", name:"Mubadala Citi DC Open - Washington", short:"Washington", tour:"WTA", type:"W500", surf:"H", draw:32, wk:29, month:7, startDate:"2025-07-19", endDate:"2025-07-27", active:false, complete:true, apiId:{wta:15977} },  // 36 matches
  { id:"montreal25", name:"Omnium Banque Nationale - Montreal", short:"Montreal", tour:"WTA", type:"W1000", surf:"H", draw:56, wk:30, month:7, startDate:"2025-07-26", endDate:"2025-08-07", active:false, complete:true, apiId:{wta:15979} },  // 100 matches
  { id:"toronto25", name:"National Bank Open - Toronto", short:"Toronto", tour:"ATP", type:"M1000", surf:"H", draw:56, wk:30, month:7, startDate:"2025-07-26", endDate:"2025-08-07", active:false, complete:true, apiId:{atp:20356} },  // 91 matches

  // ── 2025-08 ─────────────────────────
  { id:"cincinnati25", name:"Cincinnati Open - Cincinnati", short:"Cincinnati", tour:"BOTH", type:"M1000", surf:"H", draw:96, wk:32, month:8, startDate:"2025-08-05", endDate:"2025-08-18", active:false, complete:true, apiId:{atp:20357, wta:15980} },  // 208 matches
  { id:"cleveland25", name:"Tennis in the Land - Cleveland", short:"Cleveland", tour:"WTA", type:"W250", surf:"H", draw:32, wk:33, month:8, startDate:"2025-08-16", endDate:"2025-08-23", active:false, complete:true, apiId:{wta:15982} },  // 37 matches
  { id:"monterrey25", name:"Abierto GNP Seguros - Monterrey", short:"Monterrey", tour:"WTA", type:"W250", surf:"H", draw:32, wk:33, month:8, startDate:"2025-08-16", endDate:"2025-08-23", active:false, complete:true, apiId:{wta:15981} },  // 33 matches
  { id:"winstonsalem25", name:"Winston-Salem Open - Winston-Salem", short:"Winston-Salem", tour:"ATP", type:"M250", surf:"H", draw:48, wk:33, month:8, startDate:"2025-08-16", endDate:"2025-08-23", active:false, complete:true, apiId:{atp:20358} },  // 40 matches
  { id:"uso25", name:"U.S. Open - New York", short:"US Open", tour:"BOTH", type:"GS", surf:"H", draw:128, wk:34, month:8, startDate:"2025-08-18", endDate:"2025-09-07", active:false, complete:true, apiId:{atp:20359, wta:15983} },  // 291 matches

  // ── 2025-09 ─────────────────────────
  { id:"seoul25", name:"Korea Open - Seoul", short:"Seoul", tour:"WTA", type:"W500", surf:"H", draw:32, wk:37, month:9, startDate:"2025-09-13", endDate:"2025-09-21", active:false, complete:true, apiId:{wta:15987} },  // 38 matches
  { id:"beijing25", name:"China Open - Beijing", short:"Beijing", tour:"BOTH", type:"M1000", surf:"H", draw:64, wk:39, month:9, startDate:"2025-09-22", endDate:"2025-10-05", active:false, complete:true, apiId:{atp:20365, wta:15988} },  // 155 matches
  { id:"tokyo_a25", name:"Japan Open Tennis Championships - Tokyo", short:"Tokyo", tour:"ATP", type:"M500", surf:"H", draw:32, wk:39, month:9, startDate:"2025-09-22", endDate:"2025-09-30", active:false, complete:true, apiId:{atp:20364} },  // 39 matches
  { id:"shanghai25", name:"Shanghai Rolex Masters - Shanghai", short:"Shanghai", tour:"ATP", type:"M1000", surf:"H", draw:96, wk:40, month:9, startDate:"2025-09-29", endDate:"2025-10-12", active:false, complete:true, apiId:{atp:20366} },  // 100 matches

  // ── 2025-10 ─────────────────────────
  { id:"wuhan25", name:"Wuhan Open - Wuhan", short:"Wuhan", tour:"WTA", type:"W1000", surf:"H", draw:56, wk:40, month:10, startDate:"2025-10-04", endDate:"2025-10-12", active:false, complete:true, apiId:{wta:15989} },  // 73 matches
  { id:"ningbo25", name:"Ningbo Open - Ningbo", short:"Ningbo", tour:"WTA", type:"W500", surf:"H", draw:32, wk:41, month:10, startDate:"2025-10-11", endDate:"2025-10-19", active:false, complete:true, apiId:{wta:15990} },  // 43 matches
  { id:"osaka25", name:"Japan Open - Osaka", short:"Osaka", tour:"WTA", type:"W250", surf:"H", draw:32, wk:41, month:10, startDate:"2025-10-12", endDate:"2025-10-19", active:false, complete:true, apiId:{wta:15991} },  // 40 matches
  { id:"basel25", name:"Swiss Indoors Basel - Basel", short:"Basel", tour:"ATP", type:"M500", surf:"H", draw:32, wk:42, month:10, startDate:"2025-10-18", endDate:"2025-10-26", active:false, complete:true, apiId:{atp:20370} },  // 33 matches
  { id:"tokyo_w25", name:"Toray Pan Pacific Open - Tokyo", short:"Tokyo", tour:"WTA", type:"W500", surf:"H", draw:32, wk:42, month:10, startDate:"2025-10-18", endDate:"2025-10-26", active:false, complete:true, apiId:{wta:15992} },  // 41 matches
  { id:"vienna25", name:"Erste Bank Open - Vienna", short:"Vienna", tour:"ATP", type:"M500", surf:"H", draw:32, wk:42, month:10, startDate:"2025-10-18", endDate:"2025-10-26", active:false, complete:true, apiId:{atp:20371} },  // 39 matches
  { id:"paris25", name:"Rolex Paris Masters - Paris", short:"Paris", tour:"ATP", type:"M1000", surf:"H", draw:56, wk:43, month:10, startDate:"2025-10-25", endDate:"2025-11-02", active:false, complete:true, apiId:{atp:20372} },  // 70 matches

  // ── 2025-12 ─────────────────────────
  { id:"nextgen25", name:"Next Gen ATP Finals - Jeddah", short:"NextGen", tour:"ATP", type:"ATPFinals", surf:"H", draw:8, wk:51, month:12, startDate:"2025-12-17", endDate:"2025-12-21", active:false, complete:true, apiId:{atp:20376} },  // 12 matches

  // ── 2026-01 ─────────────────────────
  { id:"brisbane_w26", name:"Brisbane International - Brisbane", short:"Brisbane", tour:"WTA", type:"W500", surf:"H", draw:32, wk:1, month:1, startDate:"2026-01-02", endDate:"2026-01-11", active:false, complete:true, apiId:{wta:16701} },  // 63 matches
  { id:"auckland26", name:"ASB Classic - Auckland", short:"Auckland", tour:"BOTH", type:"M250", surf:"H", draw:28, wk:2, month:1, startDate:"2026-01-03", endDate:"2026-01-17", active:false, complete:true, apiId:{atp:21304, wta:16702} },  // 64 matches
  { id:"brisbane_a26", name:"Brisbane International - Brisbane", short:"Brisbane", tour:"ATP", type:"M250", surf:"H", draw:32, wk:1, month:1, startDate:"2026-01-03", endDate:"2026-01-11", active:false, complete:true, apiId:{atp:21301} },  // 42 matches
  { id:"hongkong_a26", name:"Hong Kong Tennis Open - Hong Kong", short:"Hong Kong", tour:"ATP", type:"M250", surf:"H", draw:28, wk:1, month:1, startDate:"2026-01-04", endDate:"2026-01-11", active:false, complete:true, apiId:{atp:21302} },  // 33 matches
  { id:"adelaide_w26", name:"Adelaide International - Adelaide", short:"Adelaide", tour:"WTA", type:"W500", surf:"H", draw:32, wk:2, month:1, startDate:"2026-01-09", endDate:"2026-01-17", active:false, complete:true, apiId:{wta:16703} },  // 44 matches
  { id:"hobart26", name:"Hobart International - Hobart", short:"Hobart", tour:"WTA", type:"W250", surf:"H", draw:32, wk:2, month:1, startDate:"2026-01-10", endDate:"2026-01-17", active:false, complete:true, apiId:{wta:16704} },  // 41 matches
  { id:"adelaide_a26", name:"Adelaide International - Adelaide", short:"Adelaide", tour:"ATP", type:"M250", surf:"H", draw:32, wk:2, month:1, startDate:"2026-01-11", endDate:"2026-01-17", active:false, complete:true, apiId:{atp:21303} },  // 27 matches
  { id:"abudhabi26", name:"Mubadala Abu Dhabi Open - Abu Dhabi", short:"Abu Dhabi", tour:"WTA", type:"W500", surf:"H", draw:32, wk:5, month:1, startDate:"2026-01-31", endDate:"2026-02-07", active:false, complete:true, apiId:{wta:16707} },  // 35 matches
  { id:"cluj26", name:"Transylvania Open - Cluj-Napoca", short:"Cluj", tour:"WTA", type:"W250", surf:"C", draw:32, wk:5, month:1, startDate:"2026-01-31", endDate:"2026-02-07", active:false, complete:true, apiId:{wta:16709} },  // 29 matches

  // ── 2026-02 ─────────────────────────
  { id:"doha_w26", name:"Qatar TotalEnergies Open - Doha", short:"Doha", tour:"WTA", type:"W500", surf:"H", draw:32, wk:6, month:2, startDate:"2026-02-06", endDate:"2026-02-14", active:false, complete:true, apiId:{wta:16710} },  // 76 matches
  { id:"dallas26", name:"Dallas Open - Dallas", short:"Dallas", tour:"ATP", type:"M250", surf:"H", draw:28, wk:6, month:2, startDate:"2026-02-07", endDate:"2026-02-15", active:false, complete:true, apiId:{atp:21308} },  // 31 matches
  { id:"delraybeach26", name:"Delray Beach Open - Delray Beach", short:"Delray", tour:"ATP", type:"M250", surf:"H", draw:32, wk:7, month:2, startDate:"2026-02-14", endDate:"2026-02-22", active:false, complete:true, apiId:{atp:21313} },  // 32 matches
  { id:"doha_a26", name:"Qatar ExxonMobil Open - Doha", short:"Doha", tour:"ATP", type:"M500", surf:"H", draw:32, wk:7, month:2, startDate:"2026-02-14", endDate:"2026-02-21", active:false, complete:true, apiId:{atp:21311} },  // 32 matches
  { id:"acapulco26", name:"Abierto Mexicano Telcel - Acapulco", short:"Acapulco", tour:"ATP", type:"M500", surf:"H", draw:32, wk:8, month:2, startDate:"2026-02-21", endDate:"2026-02-28", active:false, complete:true, apiId:{atp:21314} },  // 36 matches
  { id:"merida26", name:"Merida Open Akron - Merida", short:"Mérida", tour:"WTA", type:"W250", surf:"H", draw:32, wk:8, month:2, startDate:"2026-02-21", endDate:"2026-03-01", active:false, complete:true, apiId:{wta:16712} },  // 32 matches

  // ── 2026-04 ─────────────────────────
  { id:"montecarlo26", name:"Monte-Carlo Rolex Masters - Monte-Carlo", short:"Monte-Carlo", tour:"ATP", type:"M1000", surf:"C", draw:56, wk:14, month:4, startDate:"2026-04-04", endDate:"2026-04-12", active:false, complete:true, apiId:{atp:21322} },  // 68 matches
  { id:"linz26", name:"Upper Austria Ladies Linz - Linz", short:"Linz", tour:"WTA", type:"W500", surf:"C", draw:32, wk:14, month:4, startDate:"2026-04-05", endDate:"2026-04-12", active:false, complete:true, apiId:{wta:16718} },  // 32 matches
  { id:"barcelona26", name:"Barcelona Open Banc Sabadell - Barcelona", short:"Barcelona", tour:"ATP", type:"M500", surf:"C", draw:48, wk:15, month:4, startDate:"2026-04-11", endDate:"2026-04-19", active:false, complete:true, apiId:{atp:21323} },  // 38 matches
  { id:"munich26", name:"BMW Open - Munich", short:"Munich", tour:"ATP", type:"M250", surf:"C", draw:28, wk:15, month:4, startDate:"2026-04-11", endDate:"2026-04-19", active:false, complete:true, apiId:{atp:21324} },  // 32 matches
  { id:"stuttgart_w26", name:"Porsche Tennis Grand Prix - Stuttgart", short:"Stuttgart", tour:"WTA", type:"W500", surf:"H", draw:32, wk:15, month:4, startDate:"2026-04-11", endDate:"2026-04-19", active:false, complete:true, apiId:{wta:16719} },  // 34 matches


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
