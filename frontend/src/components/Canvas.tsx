import { useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type Node,
  type NodeTypes,
} from "@xyflow/react";
import { layoutGraph } from "../layout";
import type { ArchitectureModel, Provenance } from "../types";
import { AgentNodeView, TerminalNodeView } from "./AgentNodeView";

const nodeTypes: NodeTypes = { agent: AgentNodeView as never, terminal: TerminalNodeView as never };

const STROKE: Record<Provenance, string> = {
  confirmed: "var(--confirmed)",
  declared: "var(--declared)",
  inferred: "var(--inferred)",
  unknown: "var(--unknown)",
};
const DASH: Record<Provenance, string | undefined> = {
  confirmed: undefined,
  declared: "8 4",
  inferred: "2 4",
  unknown: undefined,
};

interface Props {
  model: ArchitectureModel;
  selected: string | null;
  onSelect: (id: string | null) => void;
}

export function Canvas({ model, selected, onSelect }: Props) {
  const { nodes, edges } = useMemo(() => {
    const ids = new Set(model.agents.map((a) => a.id));
    const terminals = new Set<string>();
    model.connections.forEach((c) => {
      if (!ids.has(c.source)) terminals.add(c.source);
      if (!ids.has(c.target)) terminals.add(c.target);
    });
    const raw: Node[] = [
      ...model.agents.map((a) => ({
        id: a.id,
        type: "agent",
        position: { x: 0, y: 0 },
        data: { agent: a, model },
        selected: a.id === selected,
      })),
      ...[...terminals].map((t) => ({
        id: t,
        type: "terminal",
        position: { x: 0, y: 0 },
        data: { label: t.replace(/^__|__$/g, "") },
        selectable: false,
      })),
    ];
    const edges: Edge[] = model.connections.map((c) => ({
      id: c.id,
      source: c.source,
      target: c.target,
      label: c.condition ?? undefined,
      labelStyle: { fontFamily: "var(--mono)", fontSize: 11 },
      markerEnd: { type: MarkerType.ArrowClosed, color: STROKE[c.provenance] },
      style: { stroke: STROKE[c.provenance], strokeWidth: 1.5, strokeDasharray: DASH[c.provenance] },
    }));
    return { nodes: layoutGraph(raw, edges), edges };
  }, [model, selected]);

  return (
    <div className="canvas">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        fitView
        onNodeClick={(_, n) => onSelect(n.type === "agent" ? n.id : null)}
        onPaneClick={() => onSelect(null)}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} color="#dfe2de" />
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="legend" aria-label="Provenance legend">
        <span className="prov confirmed">confirmed</span>
        <span className="prov declared">declared</span>
        <span className="prov inferred">inferred</span>
        <span className="prov unknown">unknown</span>
      </div>
    </div>
  );
}
