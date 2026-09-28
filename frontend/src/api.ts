import type { ArchitectureModel, DriftReport, InspectionReport, Project } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { headers: { "Content-Type": "application/json" }, ...init });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  projects: () => req<Project[]>("/api/projects"),
  register: (path: string) =>
    req<Project>("/api/projects", { method: "POST", body: JSON.stringify({ path }) }),
  inspect: (id: string) => req<InspectionReport>(`/api/projects/${id}/inspect`, { method: "POST" }),
  architecture: (id: string) =>
    req<{ stored: ArchitectureModel | null }>(`/api/projects/${id}/architecture`),
  file: (id: string, path: string) =>
    req<{ path: string; content: string }>(`/api/projects/${id}/file?path=${encodeURIComponent(path)}`),
  drift: (id: string) => req<DriftReport>(`/api/projects/${id}/drift`),
};
