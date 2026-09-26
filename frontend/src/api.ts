export interface Run {
  uuid: string;
  pk: number;
  label: string;
  process_state: string;
  exit_status: number | null;
  is_finished_ok: boolean;
  ctime: string;
  mtime: string;
  results?: Record<string, unknown>;
  children?: ChildRun[];
}
export interface ChildRun extends Run {
  execution_id: string;
  retrieved_uuid?: string;
}
export interface Resources {
  num_machines: number;
  num_mpiprocs_per_machine: number;
  num_cores_per_mpiproc: number;
  max_wallclock_seconds: number;
  max_memory_kb: number;
}
export interface Config {
  defaults: Resources;
  profile: string;
  codes: string[];
  limits: Resources & { plan_bytes: number; artifact_bytes: number };
}
export interface Artifact {
  execution_id: string;
  path: string;
  size: number;
}
export interface ReportEntry {
  level: string;
  message: string;
  time: string;
}

export function errorMessage(value: unknown): string {
  if (value instanceof Error) return value.message;
  return "The request could not be completed.";
}

export function describeDetail(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value))
    return value
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item)
          return String(item.msg);
        return "Invalid request";
      })
      .join("; ");
  return "The request was rejected.";
}

export async function api<T>(
  token: string,
  path: string,
  data?: unknown,
): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    method: data === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      ...(data === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: data === undefined ? undefined : JSON.stringify(data),
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload ? describeDetail(payload.detail) : `HTTP ${response.status}`,
    );
  }
  return response.json() as Promise<T>;
}

export async function download(
  token: string,
  uuid: string,
  artifact: Artifact,
): Promise<void> {
  const query = new URLSearchParams({
    execution_id: artifact.execution_id,
    path: artifact.path,
  });
  const response = await fetch(`/api/v1/runs/${uuid}/artifact?${query}`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      payload ? describeDetail(payload.detail) : `HTTP ${response.status}`,
    );
  }
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = artifact.path.split("/").pop() || "artifact";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function isTerminal(run: Run): boolean {
  return ["finished", "excepted", "killed"].includes(run.process_state);
}

export function readableBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / 1024 / 1024).toFixed(1)} MiB`;
}
