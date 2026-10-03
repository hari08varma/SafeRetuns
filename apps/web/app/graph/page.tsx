"use client";

import {
  type SimulationLinkDatum, type SimulationNodeDatum, forceCollide, forceLink, forceManyBody,
  forceSimulation, forceX, forceY,
} from "d3-force";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { inr } from "../../lib/format";

type Decision = {
  route: string | null; route_reason: string | null; chosen: string | null; confidence: number | null;
  risk_score: number | null; signals: string[]; eligible: boolean | null; clauses: string[]; legal: string[];
  customer_reason: string | null; desired: string | null; value_minor: number | null; rationale: string | null;
};
type Case = {
  id: string; product: string; status: string; current_node: string; created_at: string; steps: string[];
  queue: string | null; human: { action: string; at: string }[]; decision: Decision | null;
};
type Data = { generated_at: string; cases: Case[]; clauses: Record<string, string> };
type Kind = "case" | "route" | "option" | "clause" | "signal" | "reason" | "step" | "queue";
type GNode = SimulationNodeDatum & { id: string; kind: Kind; label: string; r: number; color: string; ref?: Case };
type GLink = SimulationLinkDatum<GNode> & { id: string; kind: Kind; color: string };

const ROUTE: Record<string, { label: string; color: string }> = {
  auto: { label: "Auto-resolved", color: "#2a9d8f" },
  approval: { label: "Human approval", color: "#8b7bd8" },
  escalate: { label: "Escalated", color: "#e76f51" },
  pending: { label: "In progress", color: "#8d99ae" },
};
const KIND: Record<Kind, { color: string; label: string }> = {
  case: { color: "#ffffff", label: "Return case" },
  route: { color: "#f2a541", label: "Decision route" },
  option: { color: "#f2a541", label: "Resolution chosen" },
  clause: { color: "#b9a8ef", label: "Policy clause" },
  signal: { color: "#ff8f80", label: "Risk signal" },
  reason: { color: "#ffd166", label: "Customer reason" },
  step: { color: "#5c5378", label: "Agent step" },
  queue: { color: "#7fd1c4", label: "Team queue" },
};
const human = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
const routeOf = (c: Case) => c.decision?.route ?? "pending";

function build(data: Data, showSteps: boolean, filter: string) {
  const nodes = new Map<string, GNode>();
  const links: GLink[] = [];
  const add = (id: string, kind: Kind, label: string, r: number, color: string, ref?: Case) => {
    if (!nodes.has(id)) nodes.set(id, { id, kind, label, r, color, ref });
    return id;
  };
  const link = (a: string, b: string, kind: Kind, color: string) => links.push({ id: `${a}>${b}`, source: a, target: b, kind, color });

  for (const c of data.cases) {
    const route = routeOf(c);
    if (filter !== "all" && route !== filter) continue;
    const d = c.decision;
    const value = d?.value_minor ?? 0;
    const cid = add(`case:${c.id}`, "case", `${c.product} · ${c.id}`, 7 + Math.min(9, Math.sqrt(value / 100) / 9), ROUTE[route].color, c);
    link(cid, add(`route:${route}`, "route", ROUTE[route].label, 24, ROUTE[route].color), "route", ROUTE[route].color);
    if (d?.chosen) link(cid, add(`option:${d.chosen}`, "option", human(d.chosen), 13, KIND.option.color), "option", "#f2a54155");
    if (d?.customer_reason) link(cid, add(`reason:${d.customer_reason}`, "reason", human(d.customer_reason), 11, KIND.reason.color), "reason", "#ffd16655");
    for (const cl of d?.clauses ?? []) link(cid, add(`clause:${cl}`, "clause", cl, 8, KIND.clause.color), "clause", "#b9a8ef33");
    for (const s of d?.signals ?? []) link(cid, add(`signal:${s}`, "signal", human(s), 10, KIND.signal.color), "signal", "#ff8f8077");
    if (c.queue) link(cid, add(`queue:${c.queue}`, "queue", `${human(c.queue)} queue`, 13, KIND.queue.color), "queue", "#7fd1c466");
    if (showSteps) for (const s of c.steps) link(cid, add(`step:${s}`, "step", human(s), 6, KIND.step.color), "step", "#5c537833");
  }
  return { nodes: [...nodes.values()], links };
}

export default function KnowledgeGraph() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [showSteps, setShowSteps] = useState(false);
  const [selected, setSelected] = useState<GNode | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [, setFrame] = useState(0);
  const [size, setSize] = useState({ w: 900, h: 640 });
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef<Set<string> | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const graph = useRef<{ nodes: GNode[]; links: GLink[] }>({ nodes: [], links: [] });
  const sim = useRef(forceSimulation<GNode, GLink>().stop());
  const drag = useRef<{ node?: GNode; panFrom?: { x: number; y: number; vx: number; vy: number } } | null>(null);

  // live data
  const load = useCallback(() => {
    fetch("/api/v1/public/knowledge-graph")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d: Data) => {
        const ids = new Set(d.cases.map((c) => c.id));
        if (known.current) {
          const added = [...ids].filter((i) => !known.current!.has(i));
          if (added.length) {
            setFresh(new Set(added));
            setTimeout(() => setFresh(new Set()), 12000);
          }
        }
        known.current = ids;
        setData(d);
        setError("");
      })
      .catch(() => setError("Could not reach the agent. Retrying…"));
  }, []);
  useEffect(() => {
    load();
    const t = setInterval(load, 8000);
    return () => clearInterval(t);
  }, [load]);

  // size
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // (re)build the simulation, keeping positions of nodes that already exist
  useEffect(() => {
    if (!data) return;
    const next = build(data, showSteps, filter);
    const old = new Map(graph.current.nodes.map((n) => [n.id, n]));
    next.nodes = next.nodes.map((n) => {
      const o = old.get(n.id);
      return o ? Object.assign(o, { label: n.label, r: n.r, color: n.color, ref: n.ref })
        : { ...n, x: (Math.random() - 0.5) * 200, y: (Math.random() - 0.5) * 200 };
    });
    graph.current = next;
    const s = sim.current;
    s.nodes(next.nodes)
      .force("link", forceLink<GNode, GLink>(next.links).id((n) => n.id)
        .distance((l) => (l.kind === "route" ? 110 : l.kind === "step" ? 70 : 60))
        .strength((l) => (l.kind === "route" ? 0.35 : l.kind === "step" ? 0.05 : 0.12)))
      .force("charge", forceManyBody<GNode>().strength((n) => (n.kind === "route" ? -900 : n.kind === "case" ? -140 : -90)))
      .force("collide", forceCollide<GNode>().radius((n) => n.r + 6))
      .force("x", forceX<GNode>(0).strength(0.04))
      .force("y", forceY<GNode>(0).strength(0.05))
      .alphaDecay(0.03)
      .on("tick", () => setFrame((f) => (f + 1) % 1e6))
      .alpha(old.size ? 0.4 : 1)
      .restart();
    return () => { s.on("tick", null); };
  }, [data, showSteps, filter]);

  useEffect(() => () => { sim.current.stop(); }, []);

  const neighbours = useMemo(() => {
    const m = new Map<string, Set<string>>();
    for (const l of graph.current.links) {
      const a = typeof l.source === "object" ? l.source.id : String(l.source);
      const b = typeof l.target === "object" ? l.target.id : String(l.target);
      (m.get(a) ?? m.set(a, new Set()).get(a)!).add(b);
      (m.get(b) ?? m.set(b, new Set()).get(b)!).add(a);
    }
    return m;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, showSteps, filter]);

  const focus = hover ?? selected?.id ?? null;
  const lit = (id: string) => !focus || id === focus || neighbours.get(focus)?.has(id);

  // pan / zoom / drag
  const toGraph = (e: React.PointerEvent | React.WheelEvent) => {
    const rect = box.current!.getBoundingClientRect();
    return { x: (e.clientX - rect.left - size.w / 2 - view.x) / view.k, y: (e.clientY - rect.top - size.h / 2 - view.y) / view.k };
  };
  const onWheel = (e: React.WheelEvent) => {
    const k = Math.min(3, Math.max(0.3, view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
    setView((v) => ({ ...v, k }));
  };
  const onDown = (e: React.PointerEvent, node?: GNode) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    drag.current = node ? { node } : { panFrom: { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y } };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    if (d.node) {
      const p = toGraph(e);
      d.node.fx = p.x; d.node.fy = p.y;
      sim.current.alphaTarget(0.2).restart();
    } else if (d.panFrom) {
      setView((v) => ({ ...v, x: d.panFrom!.vx + e.clientX - d.panFrom!.x, y: d.panFrom!.vy + e.clientY - d.panFrom!.y }));
    }
  };
  const onUp = () => {
    const d = drag.current;
    if (d?.node) { d.node.fx = null; d.node.fy = null; sim.current.alphaTarget(0); }
    drag.current = null;
  };

  const cases = data?.cases ?? [];
  const counts = { auto: 0, approval: 0, escalate: 0, pending: 0 } as Record<string, number>;
  cases.forEach((c) => { counts[routeOf(c)] += 1; });
  const top = useMemo(() => {
    const m = new Map<string, number>();
    cases.forEach((c) => [...(c.decision?.signals ?? []).map((s) => `Risk: ${human(s)}`),
      ...(c.decision?.customer_reason ? [`Reason: ${human(c.decision.customer_reason)}`] : [])]
      .forEach((k) => m.set(k, (m.get(k) ?? 0) + 1)));
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
  }, [cases]);

  const { nodes, links } = graph.current;

  return (
    <div className="kg">
      <div className="kg-top">
        <div>
          <h1>Decision knowledge graph</h1>
          <p className="muted">Every return the agent handled: the steps it took, what it decided, and why. Live, anonymised.</p>
        </div>
        <div className="kg-live">
          <span className={`kg-dot ${error ? "off" : ""}`} />
          {error || (data ? `Live · updated ${new Date(data.generated_at).toLocaleTimeString("en-IN")}` : "Connecting…")}
        </div>
      </div>

      <div className="kg-stats">
        <div className="tile"><div className="value">{cases.length}</div><div className="name">Cases</div></div>
        {(["auto", "approval", "escalate"] as const).map((r) => (
          <div className="tile" key={r}>
            <div className="value" style={{ color: ROUTE[r].color }}>{counts[r]}</div><div className="name">{ROUTE[r].label}</div>
          </div>
        ))}
      </div>

      <div className="kg-controls">
        <div className="tabs" role="group" aria-label="Filter by decision">
          {["all", "auto", "approval", "escalate"].map((f) => (
            <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
              {f === "all" ? "All decisions" : ROUTE[f].label}
            </button>
          ))}
        </div>
        <label className="kg-toggle">
          <input type="checkbox" checked={showSteps} onChange={(e) => setShowSteps(e.target.checked)} /> Show agent steps
        </label>
      </div>

      <div className="kg-main">
        <div className="kg-canvas" ref={box} onWheel={onWheel} onPointerDown={(e) => onDown(e)} onPointerMove={onMove}
          onPointerUp={onUp} onPointerLeave={onUp} onClick={() => setSelected(null)}>
          {data && nodes.length === 0 && <p className="kg-empty">No cases yet. Start a return from the store and watch it appear here.</p>}
          <svg width={size.w} height={size.h} role="img" aria-label="Knowledge graph of agent decisions">
            <g transform={`translate(${size.w / 2 + view.x},${size.h / 2 + view.y}) scale(${view.k})`}>
              {links.map((l) => {
                const s = l.source as GNode, t = l.target as GNode;
                if (s.x === undefined || t.x === undefined) return null;
                const on = !focus || s.id === focus || t.id === focus;
                return <line key={l.id} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke={on && focus ? l.color.slice(0, 7) : l.color}
                  strokeWidth={l.kind === "route" ? 1.8 : 1} opacity={on ? 1 : 0.08} />;
              })}
              {nodes.map((n) => {
                if (n.x === undefined || n.y === undefined) return null;
                const isCase = n.kind === "case";
                const bright = lit(n.id);
                const showLabel = n.kind === "route" || n.kind === "queue" || n.kind === "option" || n.kind === "signal"
                  || n.kind === "reason" || (focus !== null && bright) || (n.kind === "step" && showSteps);
                return (
                  <g key={n.id} transform={`translate(${n.x},${n.y})`} opacity={bright ? 1 : 0.15}
                    className={isCase && n.ref && fresh.has(n.ref.id) ? "kg-fresh" : undefined}
                    onPointerDown={(e) => onDown(e, n)} onPointerEnter={() => setHover(n.id)} onPointerLeave={() => setHover(null)}
                    onClick={(e) => { e.stopPropagation(); setSelected(n); }} style={{ cursor: "pointer" }}>
                    {n.kind === "route" && <circle r={n.r + 10} fill={n.color} opacity={0.15} />}
                    <circle r={n.r} fill={isCase ? "#1c1530" : n.color} stroke={isCase ? n.color : "rgba(255,255,255,0.35)"}
                      strokeWidth={isCase ? 3 : 1} />
                    {selected?.id === n.id && <circle r={n.r + 5} fill="none" stroke="#fff" strokeWidth={1.5} strokeDasharray="3 3" />}
                    {showLabel && (
                      <text y={n.r + 13} textAnchor="middle" className={`kg-label ${n.kind}`}>{n.label}</text>
                    )}
                  </g>
                );
              })}
            </g>
          </svg>
          <div className="kg-legend">
            {(Object.keys(KIND) as Kind[]).filter((k) => k !== "step" || showSteps).map((k) => (
              <span key={k}><i style={{ background: k === "case" ? "#1c1530" : KIND[k].color, borderColor: k === "case" ? "#fff" : "transparent" }} />{KIND[k].label}</span>
            ))}
          </div>
        </div>

        <aside className="kg-panel">
          {selected?.kind === "case" && selected.ref ? <CaseDetail c={selected.ref} clauses={data?.clauses ?? {}} />
            : selected ? <NodeDetail n={selected} cases={cases} clauses={data?.clauses ?? {}} onPick={(c) => {
              const node = nodes.find((x) => x.ref?.id === c.id);
              if (node) setSelected(node);
            }} />
            : (
              <>
                <h2>How to read it</h2>
                <p className="muted small">Each <strong>white-ringed dot</strong> is a return. It links to the <strong>decision</strong> the
                  agent made (auto, human approval or escalation), the <strong>resolution</strong> it picked, and the
                  <strong> reasons</strong>: policy clauses, risk signals and the customer&apos;s reason. Returns that share a reason
                  cluster together. Click anything to see why.</p>
                <h3>Most common reasons</h3>
                {top.length === 0 ? <p className="muted small">No decisions yet.</p> : (
                  <ul className="kg-list">{top.map(([k, n]) => <li key={k}><span>{k}</span><strong>{n}</strong></li>)}</ul>
                )}
                <h3>Latest returns</h3>
                <ul className="kg-list">
                  {cases.slice(0, 6).map((c) => (
                    <li key={c.id}><button type="button" className="kg-link" onClick={() => {
                      const node = nodes.find((x) => x.ref?.id === c.id);
                      if (node) setSelected(node);
                    }}>{c.product} · {c.id}</button>
                    <span className="kg-pill" style={{ background: ROUTE[routeOf(c)].color }}>{ROUTE[routeOf(c)].label}</span></li>
                  ))}
                </ul>
              </>
            )}
        </aside>
      </div>
    </div>
  );
}

function CaseDetail({ c, clauses }: { c: Case; clauses: Record<string, string> }) {
  const d = c.decision;
  const route = routeOf(c);
  return (
    <>
      <span className="kg-pill" style={{ background: ROUTE[route].color }}>{ROUTE[route].label}</span>
      <h2 style={{ marginTop: 8 }}>{c.product}</h2>
      <p className="muted small">Case {c.id} · {new Date(c.created_at).toLocaleString("en-IN")} · {human(c.status)}</p>
      {d ? (
        <>
          <div className="kg-metrics">
            <div><span>Value</span><strong>{inr(d.value_minor)}</strong></div>
            <div><span>Risk</span><strong>{d.risk_score?.toFixed(2) ?? "—"}</strong></div>
            <div><span>Confidence</span><strong>{d.confidence?.toFixed(2) ?? "—"}</strong></div>
          </div>
          <h3>Decision</h3>
          <p className="small"><strong>{human(d.chosen ?? "—")}</strong> · {ROUTE[route].label}</p>
          {d.route_reason && <p className="small muted">Why this route: {d.route_reason}</p>}
          {d.rationale && <p className="kg-quote">{d.rationale}</p>}
          <h3>Reasons</h3>
          <ul className="kg-reasons">
            {d.customer_reason && <li><i style={{ background: KIND.reason.color }} />Customer said: {human(d.customer_reason)}{d.desired ? `, wants ${human(d.desired).toLowerCase()}` : ""}</li>}
            {d.signals.map((s) => <li key={s}><i style={{ background: KIND.signal.color }} />Risk signal: {human(s)}</li>)}
            {d.clauses.map((cl) => <li key={cl}><i style={{ background: KIND.clause.color }} /><span><strong>{cl}</strong> {clauses[cl] ?? ""}</span></li>)}
            {d.legal.map((cl) => <li key={cl}><i style={{ background: KIND.option.color }} />Legal protection: {cl}</li>)}
          </ul>
        </>
      ) : <p className="muted small">The agent has not reached a decision for this return yet (waiting at {human(c.current_node)}).</p>}
      <h3>Steps</h3>
      <ol className="kg-steps">
        {c.steps.map((s, i) => <li key={i} className={i === c.steps.length - 1 ? "now" : ""}>{human(s)}</li>)}
      </ol>
      {c.queue && <p className="small">Sent to the <strong>{human(c.queue)}</strong> queue for the support team.</p>}
      {c.human.length > 0 && (
        <>
          <h3>People involved</h3>
          <ul className="kg-reasons">{c.human.map((h, i) => <li key={i}><i style={{ background: KIND.queue.color }} />{human(h.action.replace(".", " "))} · {new Date(h.at).toLocaleTimeString("en-IN")}</li>)}</ul>
        </>
      )}
    </>
  );
}

function NodeDetail({ n, cases, clauses, onPick }: { n: GNode; cases: Case[]; clauses: Record<string, string>; onPick: (c: Case) => void }) {
  const [kind, key] = n.id.split(":");
  const linked = cases.filter((c) => {
    const d = c.decision;
    if (kind === "route") return routeOf(c) === key;
    if (kind === "option") return d?.chosen === key;
    if (kind === "reason") return d?.customer_reason === key;
    if (kind === "clause") return d?.clauses.includes(key);
    if (kind === "signal") return d?.signals.includes(key);
    if (kind === "queue") return c.queue === key;
    if (kind === "step") return c.steps.includes(key);
    return false;
  });
  return (
    <>
      <span className="kg-pill" style={{ background: n.color, color: "#1c1530" }}>{KIND[n.kind].label}</span>
      <h2 style={{ marginTop: 8 }}>{n.label}</h2>
      {kind === "clause" && clauses[key] && <p className="kg-quote">{clauses[key]}</p>}
      <p className="muted small">{linked.length} return{linked.length === 1 ? "" : "s"} linked</p>
      <ul className="kg-list">
        {linked.slice(0, 12).map((c) => (
          <li key={c.id}><button type="button" className="kg-link" onClick={() => onPick(c)}>{c.product} · {c.id}</button>
            <span className="kg-pill" style={{ background: ROUTE[routeOf(c)].color }}>{ROUTE[routeOf(c)].label}</span></li>
        ))}
      </ul>
    </>
  );
}
