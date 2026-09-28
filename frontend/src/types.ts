// Mirrors core/models/architecture.py. Regenerate from OpenAPI with `npm run gen:types`
// (writes src/api-types.d.ts) once the backend is running; keep this file for the
// hand-picked subset the UI uses.
export type Provenance = "confirmed" | "declared" | "inferred" | "unknown";
export type Confidence = "none" | "weak" | "strong";

export interface CodeRef { file: string; symbol?: string | null; line?: number | null }
export interface SchemaField { name: string; type: string; optional: boolean }
export interface SchemaDef {
  id: string; name: string; fields: SchemaField[]; code_ref?: CodeRef | null;
  provenance: Provenance; source_adapter: string;
}
export interface ModelConfig {
  provider?: string | null; model?: string | null; temperature?: number | null;
  top_p?: number | null; max_tokens?: number | null; seed?: number | null;
}
export interface AgentNode {
  id: string; name: string; description?: string | null; model?: ModelConfig | null;
  input_schema?: string | null; output_schema?: string | null; tools: string[];
  code_ref?: CodeRef | null; provenance: Provenance; source_adapter: string;
}
export interface Connection {
  id: string; source: string; target: string; condition?: string | null;
  provenance: Provenance; source_adapter: string;
}
export interface UnresolvedItem {
  kind: string; location?: string | null; message?: string | null; source_adapter: string;
}
export interface ArchitectureModel {
  version: string; name: string; agents: AgentNode[]; schemas: SchemaDef[];
  connections: Connection[]; unresolved: UnresolvedItem[];
}
export interface AdapterStatus {
  name: string; confidence: Confidence; agents: number; schemas: number; connections: number;
  unresolved: number; notes: string[]; ran: boolean;
}
export interface InspectionReport { model: ArchitectureModel; adapters: AdapterStatus[] }
export interface Project { id: string; name: string; path: string; python?: string | null }
export interface DriftItem {
  kind: string; severity: "breaking" | "structural" | "cosmetic"; subject: string;
  node_id?: string | null; field?: string | null; architecture?: string | null;
  code?: string | null; location?: string | null;
}
export interface DriftReport { items: DriftItem[] }
