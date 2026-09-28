import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentNode, ArchitectureModel } from "../types";

export type AgentNodeData = { agent: AgentNode; model: ArchitectureModel };

export function AgentNodeView({ data, selected }: NodeProps & { data: AgentNodeData }) {
  const { agent, model } = data;
  const inS = agent.input_schema ? model.schemas.find((s) => s.id === agent.input_schema) : null;
  const outS = agent.output_schema ? model.schemas.find((s) => s.id === agent.output_schema) : null;
  return (
    <div className={`agent-node ${agent.provenance} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="name">{agent.name}</div>
      <div className="io">
        {inS?.name ?? "·"} → {outS?.name ?? "·"}
      </div>
      {agent.tools.length > 0 && (
        <div className="tools">
          {agent.tools.map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>
      )}
      {agent.code_ref && (
        <div className="file">
          {agent.code_ref.file}
          {agent.code_ref.line ? `:${agent.code_ref.line}` : ""}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export function TerminalNodeView({ data }: NodeProps & { data: { label: string } }) {
  return (
    <div className="terminal-node">
      <Handle type="target" position={Position.Left} />
      {data.label}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
