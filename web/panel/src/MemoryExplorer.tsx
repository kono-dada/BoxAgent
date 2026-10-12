import { t } from './i18n';
import { locale } from './preferences';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Network, DataSet } from 'vis-network/standalone';
import type { Bridge, MemoryEdge, MemoryGraph, OpenDetail } from './types';
const nodeStyles: Record<string, { color: string; shape: string; label: string }> = {
  EVENT: { color: '#6295ce', shape: 'dot', get label() { return t("事件"); } }, EPISODE: { color: '#a48ace', shape: 'diamond', get label() { return t("片段"); } },
  NARRATIVE: { color: '#d3a06d', shape: 'box', get label() { return t("叙事"); } }, ENTITY: { color: '#64ab94', shape: 'hexagon', get label() { return t("实体"); } },
  SESSION: { color: '#91a6ba', shape: 'triangle', get label() { return t("会话"); } }
};
const relations: Record<string, { color: string; label: string }> = {
  TEMPORAL: { color: '#779cce', get label() { return t("时间"); } }, SEMANTIC: { color: '#68aa9a', get label() { return t("语义"); } },
  CAUSAL: { color: '#d29a73', get label() { return t("因果"); } }, ENTITY: { color: '#a694cc', get label() { return t("实体"); } }
};
const subtypes: Record<string, string> = { get RELATED_TO() { return t("相关"); }, get LEADS_TO() { return t("导致"); }, get PRECEDES() { return t("先于"); }, get SUCCEEDS() { return t("后于"); }, get TEMPORALLY_CLOSE() { return t("时间接近"); } };
const clean = (value: string) => value.replace(/\s+/g, ' ').trim();
export function mergeGraph(base: MemoryGraph, patch: MemoryGraph): MemoryGraph {
  const nodes = new Map(base.nodes.map(node => [node.id, node])), edges = new Map(base.edges.map(edge => [edge.id, edge]));
  patch.nodes.forEach(node => nodes.set(node.id, node)); patch.edges.forEach(edge => edges.set(edge.id, edge));
  return { ...base, nodes: [...nodes.values()], edges: [...edges.values()] };
}
export function MemoryExplorer({ initial, bridge, query, show }: { initial: MemoryGraph; bridge: Bridge; query: string; show: OpenDetail }) {
  const language = locale();
  const [graph, setGraph] = useState(initial), [selected, setSelected] = useState<string | null>(null), [mode, setMode] = useState<'graph' | 'list'>('graph');
  const [enabled, setEnabled] = useState(new Set<string>()), [types, setTypes] = useState(new Set<string>()), [scope, setScope] = useState<'all' | 'neighbors'>('all');
  const [layout, setLayout] = useState<'force' | 'structure'>('force'), [expanding, setExpanding] = useState(false), [error, setError] = useState(''), [physics, setPhysics] = useState(false);
  const [edgeLabels, setEdgeLabels] = useState(true);
  const canvas = useRef<HTMLDivElement>(null), network = useRef<Network | null>(null), request = useRef(0), alive = useRef(true);
  const selectionCallback = useRef<(id: string | null) => void>(() => {});
  const physicsValue = useRef(physics); physicsValue.current = physics;
  const node = graph.nodes.find(row => row.id === selected);
  const neighbors = graph.edges.filter(edge => edge.source === selected || edge.target === selected);
  const relationshipTypes = [...new Set(graph.edges.map(edge => edge.type))].sort();
  const availableTypes = [...new Set(graph.nodes.map(row => row.type))].sort();
  useEffect(() => { setGraph(initial); setSelected(null); setEnabled(new Set(initial.edges.map(edge => edge.type))); setTypes(new Set(initial.nodes.map(row => row.type))); setExpanding(false); request.current++; }, [initial]);
  const visible = useMemo(() => {
    const allowed = scope === 'neighbors' && selected ? new Set([selected, ...neighbors.flatMap(edge => [edge.source, edge.target])]) : null;
    const needle = query.trim().toLowerCase();
    const nodes = graph.nodes.filter(row => types.has(row.type) && (!allowed || allowed.has(row.id)) && (!needle || `${row.content} ${row.type} ${row.id}`.toLowerCase().includes(needle)));
    const ids = new Set(nodes.map(row => row.id));
    return { nodes, edges: graph.edges.filter(edge => enabled.has(edge.type) && ids.has(edge.source) && ids.has(edge.target)) };
  }, [graph, enabled, types, query, scope, selected]);
  const graphKey = `${visible.nodes.map(row => row.id).join('|')}::${visible.edges.map(row => row.id).join('|')}`;
  selectionCallback.current = id => { setSelected(id); setError(''); };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; request.current++; };
  }, []);
  useEffect(() => {
    if (mode !== 'graph' || !canvas.current) return;
    const instance = new Network(canvas.current, { nodes: [], edges: [] }, {
      layout: { randomSeed: 17 },
      physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -70, centralGravity: .012, springLength: 170, avoidOverlap: .7, damping: .6 }, stabilization: { iterations: 220 } },
      nodes: { size: 23, borderWidth: 2, font: { size: 12, color: getComputedStyle(document.documentElement).getPropertyValue('--text'), face: '-apple-system', strokeWidth: 4, strokeColor: getComputedStyle(document.documentElement).getPropertyValue('--graph-surface') } },
      edges: { width: 1.5, arrows: { to: { enabled: true, scaleFactor: .5 } }, font: { size: 10, color: getComputedStyle(document.documentElement).getPropertyValue('--secondary'), strokeWidth: 5, strokeColor: getComputedStyle(document.documentElement).getPropertyValue('--graph-surface') }, smooth: { enabled: true, type: 'dynamic', roundness: .2 } },
      interaction: { hover: true, dragNodes: true, dragView: true, zoomView: true, keyboard: { enabled: true, bindToWindow: false }, selectConnectedEdges: false }
    });
    network.current = instance;
    instance.on('selectNode', params => { if (params.nodes[0]) selectionCallback.current(String(params.nodes[0])); });
    instance.on('deselectNode', () => selectionCallback.current(null));
    instance.on('doubleClick', params => { if (params.nodes[0]) instance.focus(params.nodes[0], { scale: 1.2, animation: { duration: 220, easingFunction: 'easeInOutQuad' } }); });
    const observer = new ResizeObserver(() => { instance.redraw(); }); observer.observe(canvas.current);
    return () => { observer.disconnect(); instance.destroy(); network.current = null; };
  }, [mode]);
  useEffect(() => {
    const update = () => {
      const styles = getComputedStyle(document.documentElement);
      network.current?.setOptions({ nodes: { font: { color: styles.getPropertyValue('--text'), strokeColor: styles.getPropertyValue('--graph-surface') } }, edges: { font: { color: styles.getPropertyValue('--secondary'), strokeColor: styles.getPropertyValue('--graph-surface') } } });
    };
    document.addEventListener('panel-theme', update); document.addEventListener('panel-preferences', update);
    return () => { document.removeEventListener('panel-theme', update); document.removeEventListener('panel-preferences', update); };
  }, []);
  useEffect(() => {
    const instance = network.current;
    if (!instance) return;
    const groups = new Map<string, MemoryEdge[]>();
    visible.edges.forEach(edge => { const pair = [edge.source, edge.target].sort().join('|'); groups.set(pair, [...groups.get(pair) || [], edge]); });
    const positions = instance.getPositions();
    const nodes = visible.nodes.map(row => {
      const style = nodeStyles[row.type] || { color: '#a3aebc', shape: 'dot', label: row.type };
      const text = clean(row.content || row.type);
      return { id: row.id, label: text.length > 18 ? `${text.slice(0, 18)}…` : text, shape: style.shape,
        level: Object.keys(nodeStyles).indexOf(row.type) + 1,
        ...(positions[row.id] && layout === 'force' ? positions[row.id] : {}),
        color: { background: style.color, border: style.color, highlight: { background: style.color, border: '#243b57' }, hover: { background: style.color, border: '#405b78' } },
        font: row.type === 'NARRATIVE' ? { color: '#fff', strokeWidth: 0 } : undefined };
    });
    const edges = visible.edges.map(edge => {
      const group = groups.get([edge.source, edge.target].sort().join('|'))!, index = group.indexOf(edge);
      return { id: edge.id, from: edge.source, to: edge.target, label: edgeLabels ? subtypes[edge.subtype || ''] || relations[edge.type]?.label || edge.type : '',
        color: { color: relations[edge.type]?.color || '#a3aebc', opacity: .8 }, physics: index === 0,
        smooth: layout === 'structure' ? false : group.length > 1 ? { enabled: true, type: index % 2 ? 'curvedCCW' : 'curvedCW', roundness: .15 + Math.floor(index / 2) * .12 } : { enabled: true, type: 'continuous', roundness: 0 } };
    });
    instance.setOptions({ layout: { hierarchical: layout === 'structure' ? { enabled: true, direction: 'UD', levelSeparation: 160, nodeSpacing: 180, sortMethod: 'directed' } : { enabled: false } }, physics: { enabled: layout === 'force' } });
    instance.setData({ nodes: new DataSet(nodes), edges: new DataSet(edges) });
    let timer: ReturnType<typeof setTimeout>;
    if (layout === 'force') {
      instance.startSimulation(); timer = setTimeout(() => { instance.stopSimulation(); instance.setOptions({ physics: { enabled: physicsValue.current } }); instance.fit({ animation: false }); }, 650);
    } else { timer = setTimeout(() => instance.fit({ animation: false }), 50); }
    return () => clearTimeout(timer);
  }, [graphKey, layout, mode, edgeLabels, language]);
  useEffect(() => { if (network.current) { if (selected && visible.nodes.some(row => row.id === selected)) network.current.selectNodes([selected]); else network.current.unselectAll(); } }, [selected, visible]);
  useEffect(() => { network.current?.setOptions({ physics: { enabled: physics && layout === 'force' } }); }, [physics, layout]);
  async function expand() {
    if (!node) return;
    const mine = ++request.current, id = node.id; setExpanding(true); setError('');
    try {
      const patch = await bridge.request('memory_snapshot', { selected_id: id, node_limit: 100, edge_limit: 500 }) as MemoryGraph;
      if (alive.current && mine === request.current) {
        setGraph(current => mergeGraph(current, patch));
        setEnabled(current => new Set([...current, ...patch.edges.map(edge => edge.type)]));
        setTypes(current => new Set([...current, ...patch.nodes.map(row => row.type)]));
      }
    } catch (reason) { if (alive.current && mine === request.current) setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { if (alive.current && mine === request.current) setExpanding(false); }
  }
  function toggle(set: Set<string>, value: string, update: (value: Set<string>) => void) { const next = new Set(set); next.has(value) ? next.delete(value) : next.add(value); update(next); }
  return <div className="memory-explorer">
    <div className="explorer-heading"><div><strong>{graph.statistics?.node_count ?? graph.nodes.length}</strong><span>{t("个节点")}</span><strong>{graph.statistics?.edge_count ?? graph.edges.length}</strong><span>{t("条关系")}</span><small>{t('当前载入 {0} 个节点 / {1} 条关系', graph.nodes.length, graph.edges.length)}</small></div><div className="segmented"><button aria-pressed={mode === 'graph'} onClick={() => setMode('graph')}>{t("关系图")}</button><button aria-pressed={mode === 'list'} onClick={() => setMode('list')}>{t("列表")}</button></div></div>
    <div className="explorer-grid"><section className="graph-surface">
      <div className="graph-toolbar"><div className="segmented"><button aria-pressed={layout === 'force'} onClick={() => setLayout('force')}>{t("自由布局")}</button><button aria-pressed={layout === 'structure'} onClick={() => setLayout('structure')}>{t("类型分层")}</button></div><button className="button" onClick={() => network.current?.fit({ animation: { duration: 220, easingFunction: 'easeInOutQuad' } })}>{t("居中")}</button><button className="button" onClick={() => { setPhysics(!physics); }} aria-pressed={physics}>{physics ? t("暂停布局") : t("动态布局")}</button></div>
      <div className="graph-filters">{availableTypes.map(type => <button key={type} aria-pressed={types.has(type)} onClick={() => toggle(types, type, setTypes)}><i style={{ background: nodeStyles[type]?.color || '#a3aebc' }}/>{nodeStyles[type]?.label || type}</button>)}<span className="filter-divider"/>{relationshipTypes.map(type => <button key={type} data-relation={type} aria-pressed={enabled.has(type)} onClick={() => toggle(enabled, type, setEnabled)}>{relations[type]?.label || type}</button>)}<span className="filter-divider"/><button aria-pressed={edgeLabels} onClick={() => setEdgeLabels(!edgeLabels)}>{t("关系标签")}</button></div>
      {mode === 'graph' ? <div key="canvas" className="memory-canvas" ref={canvas} role="region" aria-label={t("记忆关系图，可拖动节点、滚轮缩放")} tabIndex={0}/> : <div key="list" className="memory-node-list">{visible.nodes.map(row => <button className={`memory-node-row ${selected === row.id ? 'active' : ''}`} key={row.id} onClick={() => { setSelected(row.id); setError(''); }}><span className="badge">{nodeStyles[row.type]?.label || row.type}</span><span>{row.content || row.id}</span></button>)}</div>}
      {!visible.nodes.length && <div className="graph-no-results">{graph.nodes.length ? t("没有匹配的节点，试试调整筛选。") : t("记忆库还没有节点。")}</div>}
      <div className="graph-footer"><span>{t('{0} 个可见节点 · {1} 条可见关系', visible.nodes.length, visible.edges.length)}</span><span>{t("拖拽节点 · 滚轮缩放 · 双击聚焦")}</span></div>
    </section><section className="graph-inspector">
      <header><h2>{node ? t("节点与关系") : t("探索记忆结构")}</h2>{node && <button className="text-button" onClick={() => { setSelected(null); setScope('all'); }}>{t("清除")}</button>}</header>
      {node ? <><span className="badge">{nodeStyles[node.type]?.label || node.type}</span><p className="inspector-content">{node.content || t("没有文本内容")}</p><dl className="node-fields"><div><dt>{t("节点 ID")}</dt><dd>{node.id}</dd></div><div><dt>{t("来源")}</dt><dd>{node.source || t("未记录")}</dd></div><div><dt>{t("记录时间")}</dt><dd>{node.timestamp ? new Date(node.timestamp).toLocaleString(locale()) : t("未记录")}</dd></div></dl>
        <div className="node-actions"><button className="button" onClick={() => void expand()} disabled={expanding}>{expanding ? t("正在载入…") : t("展开邻居")}</button><button className="button" aria-pressed={scope === 'neighbors'} onClick={() => setScope(scope === 'neighbors' ? 'all' : 'neighbors')}>{scope === 'neighbors' ? t("查看全部") : t("只看邻居")}</button></div>
        {error && <p role="alert" className="console-warning">{error}</p>}
        <h3>{t("已载入关系")}<span>{neighbors.length}</span></h3>{neighbors.length ? <div className="neighbor-list">{neighbors.map(edge => { const id = edge.source === node.id ? edge.target : edge.source, other = graph.nodes.find(row => row.id === id); return <button key={edge.id} onClick={() => { setSelected(id); setError(''); network.current?.focus(id, { scale: 1.1, animation: { duration: 220, easingFunction: 'easeInOutQuad' } }); }}><span className="neighbor-relation">{edge.source === node.id ? '→' : '←'} {relations[edge.type]?.label || edge.type} / {subtypes[edge.subtype || ''] || edge.subtype || edge.type}</span><span>{other?.content || id}</span></button>; })}</div> : <p className="metadata">{t("当前载入范围没有关联。展开邻居可查询此节点的直接关系。")}</p>}
        <button className="text-button" onClick={event => show(node, t("记忆原始记录"), node.id, event.currentTarget)}>{t("查看完整字段 →")}</button>
        <button className="text-button danger" disabled={expanding} onClick={() => { if (!confirm(t("删除此记忆节点及其关系？此操作无法撤销。"))) return; setExpanding(true); void bridge.request('delete_memory_node', {memory_id:node.id}).then(() => { if (!alive.current) return; setGraph(current => ({...current, nodes:current.nodes.filter(row=>row.id!==node.id), edges:current.edges.filter(edge=>edge.source!==node.id&&edge.target!==node.id), statistics:undefined})); setSelected(null); setScope('all'); }).catch(reason=>{if(alive.current)setError(reason.message);}).finally(()=>{if(alive.current)setExpanding(false);}); }}>{t("删除此记忆")}</button>
      </> : <><div className="inspector-placeholder">◎</div><p>{t("点击图中的节点，查看正文、来源与关联。")}</p><p className="metadata">{t("也可以切换列表，用键盘选中节点。关系方向和类型来自记忆库的实际记录。")}</p><div className="structure-key">{Object.entries(nodeStyles).map(([type, style]) => <span key={type}><i style={{ background: style.color }}/>{style.label}<small>{type}</small></span>)}</div></>}
    </section></div>
  </div>;
}
