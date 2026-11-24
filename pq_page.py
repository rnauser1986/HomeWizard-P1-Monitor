PQ_HTML = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Power quality – HomeWizard P1</title>
<style>
body{margin:0;font-family:sans-serif;background:#0b0f17;color:#e2e8f0}
.w{max-width:1560px;width:95%;margin:20px auto;padding:0 16px}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:14px}
.btn{background:#121826;color:#e2e8f0;border:1px solid #263042;border-radius:8px;padding:6px 12px;cursor:pointer;text-decoration:none;font-size:13px}
.btn.a{border-color:#a855f7;background:#1a132b;color:#e9d5ff}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}
.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}
@media(max-width:1050px){.grid4{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:800px){.grid3,.grid4{grid-template-columns:1fr}}
.card{background:#121826;border:1px solid #263042;border-radius:10px;padding:12px}
.sub{font-size:12px;color:#94a3b8;margin-top:4px}
.panel{background:#090c12;border:1px solid #1e2636;border-radius:10px;padding:16px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:7px 8px;border-bottom:1px solid #1a2232;text-align:left}
th{color:#94a3b8;font-weight:600}
</style></head><body><div class="w">
<div class="top">
  <div style="display:flex;align-items:center;gap:12px"><a class="btn" href="/">← Statistics</a><h3 style="margin:0">Power quality &amp; health graph</h3></div>
  <div style="display:flex;gap:6px">
    <button type="button" class="btn" data-h="6">6h</button><button type="button" class="btn a" data-h="24">24h</button>
    <button type="button" class="btn" data-h="72">72h</button><button type="button" class="btn" data-h="168">1 week</button>
    <button type="button" class="btn" data-h="720">1 month</button>
  </div>
</div>
<div class="grid3" id="cards"></div><div class="grid4" id="agg-cards"></div>
<div class="panel"><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;color:#94a3b8"><span>Voltage per phase (L1 blue · L2 purple · L3 green, 200–245 V)</span><span style="display:flex;gap:14px"><span id="v-lbl">Scale 200 – 245 V</span><span id="pts-lbl"></span></span></div><svg id="svg-v" viewBox="0 0 1200 175" style="width:100%;height:175px;display:block;background:#06080d;border-radius:8px"></svg></div>
<div class="panel"><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;color:#94a3b8"><span>Current per phase (A1 cyan · A2 pink · A3 yellow, 0–35 A)</span><span id="a-lbl">Scale 0 – 35 A</span></div><svg id="svg-a" viewBox="0 0 1200 155" style="width:100%;height:155px;display:block;background:#06080d;border-radius:8px"></svg></div>
<div class="panel"><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;color:#94a3b8"><span>Phase balance (current imbalance): max(A) − min(A) (A)</span><span id="imb-lbl"></span></div><svg id="svg-imb" viewBox="0 0 1200 140" style="width:100%;height:140px;display:block;background:#06080d;border-radius:8px"></svg></div>
<div class="panel"><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;color:#94a3b8"><span>Total active power – minute max (W)</span><span id="w-lbl"></span></div><svg id="svg-w" viewBox="0 0 1200 150" style="width:100%;height:150px;display:block;background:#06080d;border-radius:8px"></svg></div>
<div class="panel"><div style="display:flex;justify-content:space-between;margin-bottom:8px;font-size:13px;color:#94a3b8"><span>Voltage spread between phases: max(V) − min(V) (V)</span><span id="dv-lbl"></span></div><svg id="svg-dv" viewBox="0 0 1200 140" style="width:100%;height:140px;display:block;background:#06080d;border-radius:8px"></svg></div>
<div class="panel"><div style="font-size:14px;font-weight:600;margin-bottom:8px">Anomalies &amp; peaks</div><div style="overflow-x:auto"><table><thead><tr><th>Time</th><th>L1 V</th><th>L2 V</th><th>L3 V</th><th>ΔV (max−min)</th><th>L1 A</th><th>L2 A</th><th>L3 A</th><th>Power</th></tr></thead><tbody id="ev"></tbody></table></div></div>
</div>
<script>
const fmtTs = ts => {
  if (!ts) return "-";
  const d = new Date(ts);
  return isNaN(d) ? String(ts) : d.toLocaleString("sv-SE", {timeZone:"Europe/Stockholm",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
};
let curH = 24;
async function loadPq(h) {
  curH = h || curH;
  document.querySelectorAll("[data-h]").forEach(b => b.classList.toggle("a", Number(b.dataset.h) === curH));
  const d = await (await fetch("/api/power-quality?hours=" + curH)).json();
  const s = d.summary || {}, a = d.aggregates || {};
  const wThr = Number(a.power_threshold_w || 8000), dvThr = Number(a.dv_threshold_v || 10);
  document.getElementById("cards").innerHTML = [1,2,3].map(i => {
    const mn = s["min_v"+i], mx = s["max_v"+i], ma = s["max_a"+i];
    const sp = (mn!=null&&mx!=null) ? (mx-mn).toFixed(1) : "-";
    const ac = (ma!=null&&Number(ma)>=20) ? "#f59e0b" : "#34d399";
    return `<div class="card"><div style="font-size:12px;color:#94a3b8">PHASE L${i}</div><div style="font-size:20px;font-weight:700;margin-top:4px">${mn??"-"} – ${mx??"-"} V</div><div class="sub">Range Δ ${sp} V · Max current <strong style="color:${ac}">${ma??"-"} A</strong></div></div>`;
  }).join("");
  const mw = s.max_w!=null?Number(s.max_w):null, mdv = s.max_dv!=null?Number(s.max_dv):null;
  document.getElementById("agg-cards").innerHTML =
    `<div class="card"><div style="font-size:12px;color:#94a3b8">POWER PEAK (${d.hours ?? curH}h)</div><div style="font-size:20px;font-weight:700;margin-top:4px;color:${mw!=null&&mw>=wThr?"#f59e0b":"#e2e8f0"}">${mw!=null?mw+" W":"-"}</div><div class="sub">Time: ${fmtTs(s.max_w_ts)} · ≥${wThr} W: ${a.high_w_cnt??0}</div></div>` +
    `<div class="card"><div style="font-size:12px;color:#94a3b8">MAX VOLTAGE SPREAD (ΔV)</div><div style="font-size:20px;font-weight:700;margin-top:4px;color:${mdv!=null&&mdv>=dvThr?"#f59e0b":"#38bdf8"}">${mdv!=null?mdv.toFixed(1)+" V":"-"}</div><div class="sub">Time: ${fmtTs(s.max_dv_ts)} · ≥${dvThr} V: ${a.high_dv_cnt??0}</div></div>` +
    `<div class="card"><div style="font-size:12px;color:#94a3b8">VOLTAGE HISTORY (${d.hours ?? curH}h)</div><div style="font-size:16px;font-weight:700;margin-top:6px"><215V: ${a.low_v_cnt??0} · >245V: ${a.high_v_cnt??0}</div><div class="sub">Samples: ${a.samples_cnt??0}</div></div>` +
    `<div class="card"><div style="font-size:12px;color:#94a3b8">CURRENT PEAKS ≥20 A (${d.hours ?? curH}h)</div><div style="font-size:20px;font-weight:700;margin-top:4px;color:${(a.high_a_cnt||0)>0?"#f59e0b":"#34d399"}">${a.high_a_cnt??0}</div><div class="sub">L1: ${a.high_a_l1_cnt??0} · L2: ${a.high_a_l2_cnt??0} · L3: ${a.high_a_l3_cnt??0}</div></div>`;
  const ser = d.series || [];
  const imbOf = r => {
    const vals = [r.a1_max, r.a2_max, r.a3_max].map(Number).filter(Number.isFinite);
    return vals.length ? (Math.max(...vals) - Math.min(...vals)) : 0;
  };
  document.getElementById("pts-lbl").textContent = ser.length + " samples";
  const svgV = document.getElementById("svg-v"), svgA = document.getElementById("svg-a"), svgW = document.getElementById("svg-w"), svgDv = document.getElementById("svg-dv");
  if (!ser.length) {
    if(svgV)svgV.innerHTML = "<text x='20' y='40' fill='#94a3b8'>No data</text>";
    svgW.innerHTML = ""; svgDv.innerHTML = ""; const svgImbEl = document.getElementById("svg-imb"); if (svgImbEl) svgImbEl.innerHTML = "";
    document.getElementById("ev").innerHTML = "<tr><td colspan='9' style='color:#94a3b8'>No anomalies in the interval</td></tr>";
    return;
  }
  const W = 1200, pL = 48, pR = 48, pT = 16, pB = 24, pw = W - pL - pR;
  const xOf = i => pL + (ser.length === 1 ? pw / 2 : (i / (ser.length - 1)) * pw);
  const pl = (fn, yfn, c, w) => `<polyline fill='none' stroke='${c}' stroke-width='${w}' points='${ser.map((r,i)=>xOf(i).toFixed(1)+","+yfn(fn(r)).toFixed(1)).join(" ")}'/>`;
  const ph = 250 - pT - pB;
  const vph = 175 - pT - pB, aph = 155 - pT - pB;
  const yV = v => pT + vph - ((Math.min(245, Math.max(200, Number(v || 230))) - 200) / 45) * vph;
  const yA = a => pT + aph - (Math.min(35, Math.max(0, Number(a || 0))) / 35) * aph;
  if(svgV)svgV.innerHTML = `<line x1='${pL}' y1='${pT}' x2='${W-pR}' y2='${pT}' stroke='#1e293b'/><line x1='${pL}' y1='${pT+ph}' x2='${W-pR}' y2='${pT+ph}' stroke='#1e293b'/><text x='8' y='${pT+8}' fill='#94a3b8' font-size='11'>245V</text><line x1='${pL}' y1='${yV(215).toFixed(1)}' x2='${W-pR}' y2='${yV(215).toFixed(1)}' stroke='#ef4444' stroke-width='1' stroke-dasharray='4,4' opacity='0.7'/><text x='8' y='${(yV(215)+3).toFixed(1)}' fill='#ef4444' font-size='10' opacity='0.85'>215V</text><text x='8' y='${pT+vph}' fill='#94a3b8' font-size='11'>200V</text>` + pl(r=>r.v1,yV,"#38bdf8",1.6) + pl(r=>r.v2,yV,"#a78bfa",1.6) + pl(r=>r.v3,yV,"#34d399",1.6) ;
  if(svgA)svgA.innerHTML = `<line x1="${pL}" y1="${pT}" x2="${W-pR}" y2="${pT}" stroke="#1e293b"/><line x1="${pL}" y1="${pT+aph}" x2="${W-pR}" y2="${pT+aph}" stroke="#1e293b"/><text x="8" y="${pT+8}" fill="#94a3b8" font-size="11">35A</text><line x1="${pL}" y1="${yA(20).toFixed(1)}" x2="${W-pR}" y2="${yA(20).toFixed(1)}" stroke="#ef4444" stroke-width="1" stroke-dasharray="4,4" opacity="0.75"/><text x="8" y="${(yA(20)+3).toFixed(1)}" fill="#ef4444" font-size="10" opacity="0.9">20A</text><text x="8" y="${pT+aph}" fill="#94a3b8" font-size="11">0A</text>` + pl(r=>r.a1_max,yA,"#22d3ee",1.5) + pl(r=>r.a2_max,yA,"#e879f9",1.5) + pl(r=>r.a3_max,yA,"#f59e0b",1.7);
  const wTop = Math.ceil(Math.max(15000, ...ser.map(r=>Number(r.w_max||0))) / 1000) * 1000, wph = 150 - pT - pB;
  const yW = val => pT + wph - (Math.max(0, Number(val || 0)) / wTop) * wph;
  document.getElementById("w-lbl").textContent = "Scale 0 – " + wTop + " W";
  svgW.innerHTML = `<line x1='${pL}' y1='${pT}' x2='${W-pR}' y2='${pT}' stroke='#1e293b'/><line x1='${pL}' y1='${pT+wph}' x2='${W-pR}' y2='${pT+wph}' stroke='#1e293b'/><text x='6' y='${pT+8}' fill='#94a3b8' font-size='11'>${wTop}W</text>${wTop >= 9500 ? `<line x1='${pL}' y1='${yW(9500).toFixed(1)}' x2='${W-pR}' y2='${yW(9500).toFixed(1)}' stroke='#ef4444' stroke-width='1' stroke-dasharray='4,4' opacity='0.75'/><text x='6' y='${(yW(9500)+3).toFixed(1)}' fill='#ef4444' font-size='10' opacity='0.9'>9500W</text>` : ''}<text x='6' y='${pT+wph}' fill='#94a3b8' font-size='11'>0W</text>` + pl(r=>r.w_max,yW,"#f59e0b",1.8);
  const svgImb = document.getElementById("svg-imb");
  const imbTop = Math.ceil(Math.max(10, ...ser.map(imbOf)) / 5) * 5, iph = 140 - pT - pB;
  const yImb = val => pT + iph - (Math.max(0, Number(val || 0)) / imbTop) * iph;
  const imbLbl = document.getElementById("imb-lbl");
  if (imbLbl) imbLbl.textContent = "Scale 0 – " + imbTop.toFixed(0) + " A";
  if (svgImb) svgImb.innerHTML = `<line x1='${pL}' y1='${pT}' x2='${W-pR}' y2='${pT}' stroke='#1e293b'/><line x1='${pL}' y1='${pT+iph}' x2='${W-pR}' y2='${pT+iph}' stroke='#1e293b'/><text x='6' y='${pT+8}' fill='#94a3b8' font-size='11'>${imbTop}A</text>${imbTop >= 12 ? `<line x1='${pL}' y1='${yImb(12).toFixed(1)}' x2='${W-pR}' y2='${yImb(12).toFixed(1)}' stroke='#ef4444' stroke-width='1' stroke-dasharray='4,4' opacity='0.75'/><text x='6' y='${(yImb(12)+3).toFixed(1)}' fill='#ef4444' font-size='10' opacity='0.9'>12A</text>` : ''}<text x='6' y='${pT+iph}' fill='#94a3b8' font-size='11'>0A</text>` + pl(imbOf, yImb, "#fb7185", 1.8);
  const dvTop = Math.ceil(Math.max(15, ...ser.map(r=>Number(r.dv_max||0))) / 5) * 5, dph = 140 - pT - pB;
  const yDv = val => pT + dph - (Math.max(0, Number(val || 0)) / dvTop) * dph;
  document.getElementById("dv-lbl").textContent = "Scale 0 – " + dvTop.toFixed(0) + " V";
  svgDv.innerHTML = `<line x1='${pL}' y1='${pT}' x2='${W-pR}' y2='${pT}' stroke='#1e293b'/><line x1='${pL}' y1='${pT+dph}' x2='${W-pR}' y2='${pT+dph}' stroke='#1e293b'/><text x='6' y='${pT+8}' fill='#94a3b8' font-size='11'>${dvTop}V</text>${dvTop >= 8 ? `<line x1='${pL}' y1='${yDv(8).toFixed(1)}' x2='${W-pR}' y2='${yDv(8).toFixed(1)}' stroke='#ef4444' stroke-width='1' stroke-dasharray='4,4' opacity='0.75'/><text x='6' y='${(yDv(8)+3).toFixed(1)}' fill='#ef4444' font-size='10' opacity='0.9'>8V</text>` : ''}<text x='6' y='${pT+dph}' fill='#94a3b8' font-size='11'>0V</text>` + pl(r=>r.dv_max,yDv,"#38bdf8",1.8);
  window._pqSer = ser;
  window._pqHoverCtx = window._pqHoverCtx || {};
  const tip = document.getElementById("pq-tip") || (() => {
    const d = document.createElement("div");
    d.id = "pq-tip";
    d.style.cssText = "position:fixed;pointer-events:none;z-index:99999;display:none;background:#0b1020;border:1px solid #475569;border-radius:8px;padding:8px 10px;font-size:12px;line-height:1.45;color:#e2e8f0;box-shadow:0 8px 24px rgba(0,0,0,.5);white-space:nowrap";
    document.body.appendChild(d);
    return d;
  })();
  const ns = "http://www.w3.org/2000/svg";
  const mkSvg = (t, a) => { const e = document.createElementNS(ns, t); for (const [k, v] of Object.entries(a)) e.setAttribute(k, String(v)); return e; };

  function bindGraphHover(el, kind, h, y1f, y2f) {
    if (!el) return;
    el.style.cursor = "crosshair";
    const line = mkSvg("line", { x1: pL, x2: pL, y1: pT, y2: pT + h, stroke: "#cbd5e1", "stroke-width": 1.2, "stroke-dasharray": "4,3", visibility: "hidden" });
    const cols = kind === "v" ? ["#38bdf8", "#a78bfa", "#34d399"] : [kind === "w" ? "#f59e0b" : (kind === "imb" ? "#fb7185" : "#38bdf8")];
    const dots = cols.map(c => mkSvg("circle", { cx: pL, cy: pT, r: kind === "v" ? 3.5 : 4, fill: c, stroke: "#0b1020", "stroke-width": 1.2, visibility: "hidden" }));
    el.appendChild(line);
    dots.forEach(d => el.appendChild(d));

    if (el.dataset.pqBound === "1") return;
    el.dataset.pqBound = "1";

    const hide = () => {
      line.setAttribute("visibility", "hidden");
      dots.forEach(d => d.setAttribute("visibility", "hidden"));
      tip.style.display = "none";
    };
    el.addEventListener("mouseleave", hide);
    el.addEventListener("mousemove", ev => {
      const arr = window._pqSer || [];
      const ctx = (window._pqHoverCtx && window._pqHoverCtx[kind]) || {};
      const cW = ctx.W || W, cpL = ctx.pL ?? pL, cpR = ctx.pR ?? pR, cpT = ctx.pT ?? pT, cpw = ctx.pw || pw, ch = ctx.h || h;
      const cy1 = ctx.y1f || y1f;
      const cxOf = ctx.xOf || (i => cpL + (arr.length <= 1 ? cpw / 2 : (i / (arr.length - 1)) * cpw));
      if (!arr.length) return hide();
      if (!line.parentNode) el.appendChild(line);
      line.setAttribute("y1", cpT);
      line.setAttribute("y2", cpT + ch);
      dots.forEach(d => { if (!d.parentNode) el.appendChild(d); });
      const rect = el.getBoundingClientRect();
      if (!rect.width) return;
      let vx = ((ev.clientX - rect.left) / rect.width) * cW;
      const ctm = el.getScreenCTM && el.getScreenCTM();
      if (ctm && el.createSVGPoint) {
        const pt = el.createSVGPoint();
        pt.x = ev.clientX;
        pt.y = ev.clientY;
        const svgPt = pt.matrixTransform(ctm.inverse());
        if (Number.isFinite(svgPt.x)) vx = svgPt.x;
      }
      const clx = Math.max(cpL, Math.min(cW - cpR, vx));
      const idx = arr.length === 1 ? 0 : Math.max(0, Math.min(arr.length - 1, Math.round(((clx - cpL) / cpw) * (arr.length - 1))));
      const r = arr[idx] || {};
      const gx = Number(cxOf(idx)).toFixed(1);

      line.setAttribute("x1", gx);
      line.setAttribute("x2", gx);
      line.setAttribute("visibility", "visible");

      let body = "";
      if (kind === "v") {
        const ys = [cy1(r.v1), cy1(r.v2), cy1(r.v3)];
        dots.forEach((d, i) => {
          d.setAttribute("cx", gx);
          d.setAttribute("cy", Number.isFinite(ys[i]) ? ys[i].toFixed(1) : String(pT));
          d.setAttribute("visibility", "visible");
        });
        body = `<div><span style='color:#38bdf8'>L1: ${r.v1 ?? "-"} V</span> · <span style='color:#a78bfa'>L2: ${r.v2 ?? "-"} V</span> · <span style='color:#34d399'>L3: ${r.v3 ?? "-"} V</span></div>`;
      } else if (kind === "a") {
        const ys = [cy1(r.a1_max), cy1(r.a2_max), cy1(r.a3_max)];
        dots.forEach((d, i) => { d.setAttribute("cx", gx); d.setAttribute("cy", Number.isFinite(ys[i]) ? ys[i].toFixed(1) : String(pT)); d.setAttribute("visibility", "visible"); });
        body = `<div><span style='color:#22d3ee'>A1: ${r.a1_max ?? r.a1 ?? "-"} A</span> · <span style='color:#e879f9'>A2: ${r.a2_max ?? r.a2 ?? "-"} A</span> · <span style='color:#f59e0b'>A3: ${r.a3_max ?? r.a3 ?? "-"} A</span></div>`;
      } else if (kind === "w") {
        const yw = cy1(r.w_max);
        dots[0].setAttribute("cx", gx);
        dots[0].setAttribute("cy", Number.isFinite(yw) ? yw.toFixed(1) : String(pT));
        dots[0].setAttribute("visibility", "visible");
        body = `<div style='color:#f59e0b'>Max power: ${r.w_max ?? "-"} W</div>`;
      } else if (kind === "imb") {
        const val = imbOf(r);
        const yi = cy1(val);
        dots[0].setAttribute("cx", gx);
        dots[0].setAttribute("cy", Number.isFinite(yi) ? yi.toFixed(1) : String(pT));
        dots[0].setAttribute("visibility", "visible");
        body = `<div style='color:#fb7185'>Phase imbalance: ${val.toFixed(1)} A</div><div style='color:#94a3b8'>A1: ${r.a1_max ?? r.a1 ?? "-"} A · A2: ${r.a2_max ?? r.a2 ?? "-"} A · A3: ${r.a3_max ?? r.a3 ?? "-"} A</div>`;
      } else {
        const yd = cy1(r.dv_max);
        dots[0].setAttribute("cx", gx);
        dots[0].setAttribute("cy", Number.isFinite(yd) ? yd.toFixed(1) : String(pT));
        dots[0].setAttribute("visibility", "visible");
        body = `<div style='color:#38bdf8'>ΔV: ${r.dv_max != null ? Number(r.dv_max).toFixed(1) + " V" : "-"}</div>`;
      }

      tip.innerHTML = `<div style='font-weight:600;margin-bottom:3px;color:#f8fafc'>${fmtTs(r.bucket_utc)}</div>${body}`;
      tip.style.display = "block";
      const tr = tip.getBoundingClientRect();
      let left = ev.clientX + 14, top = ev.clientY + 14;
      if (left + tr.width > window.innerWidth - 8) left = Math.max(8, ev.clientX - tr.width - 14);
      if (top + tr.height > window.innerHeight - 8) top = Math.max(8, ev.clientY - tr.height - 14);
      tip.style.left = left + "px";
      tip.style.top = top + "px";
    });
  }

  window._pqHoverCtx.v = { W, pL, pR, pT, pw, h: vph, y1f: yV, xOf };
  window._pqHoverCtx.a = { W, pL, pR, pT, pw, h: aph, y1f: yA, xOf };
  window._pqHoverCtx.imb = { W, pL, pR, pT, pw, h: iph, y1f: yImb, xOf };
  window._pqHoverCtx.w = { W, pL, pR, pT, pw, h: wph, y1f: yW, xOf };
  window._pqHoverCtx.dv = { W, pL, pR, pT, pw, h: dph, y1f: yDv, xOf };
  bindGraphHover(svgV, "v", vph, yV, null);
  bindGraphHover(svgA, "a", aph, yA, null);
  bindGraphHover(svgImb, "imb", iph, yImb, null);
  bindGraphHover(svgW, "w", wph, yW, null);
  bindGraphHover(svgDv, "dv", dph, yDv, null);

  const vCell = v => { const n = Number(v), b = !Number.isNaN(n) && (n < 215 || n > 245); return `<td${b?" style='color:#ef4444;font-weight:600'":""}>${v??"-"} V</td>`; };
  const aCell = a => { const n = Number(a), h = !Number.isNaN(n) && n >= 20; return `<td${h?" style='color:#f59e0b;font-weight:600'":""}>${a??"-"} A</td>`; };
  const dvCell = dv => { const n = Number(dv), h = !Number.isNaN(n) && n >= dvThr; return `<td${h?" style='color:#38bdf8;font-weight:600'":""}>${dv!=null?n.toFixed(1)+" V":"-"}</td>`; };
  const wCell = w => { const n = Number(w), h = !Number.isNaN(n) && n >= wThr; return `<td${h?" style='color:#f59e0b;font-weight:600'":""}>${w!=null?n+" W":"-"}</td>`; };
  const ev = d.events || [];
  document.getElementById("ev").innerHTML = ev.length
    ? ev.map(x => `<tr><td>${fmtTs(x.ts_utc)}</td>${vCell(x.v1)}${vCell(x.v2)}${vCell(x.v3)}${dvCell(x.dv)}${aCell(x.a1)}${aCell(x.a2)}${aCell(x.a3)}${wCell(x.w)}</tr>`).join("")
    : "<tr><td colspan='9' style='color:#94a3b8'>No anomalies in the interval</td></tr>";
}
document.querySelectorAll("[data-h]").forEach(b => b.addEventListener("click", () => loadPq(Number(b.dataset.h))));
loadPq(24);
</script></body></html>'''
