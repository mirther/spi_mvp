import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  Database,
  FileInput,
  FileOutput,
  FolderKanban,
  HardDrive,
  History,
  Layers,
  LogOut,
  KeyRound,
  Play,
  Plus,
  Search,
  Server,
  Settings,
  Terminal,
  Trash2,
  Workflow,
  X,
} from "lucide-react";
import segyInDefinition from "./modules/definitions/segyin.json";
import segyOutDefinition from "./modules/definitions/segyout.json";
import "./App.css";

type Page = "Projects" | "Data Assets" | "Cluster Configurations" | "Settings";

type ModuleParameter = {
  name: string;
  label: string;
  type: string;
  required: boolean;
  description: string;
};

type ModuleDefinition = {
  id: string;
  name: string;
  category: string;
  description: string;
  inputs: string[];
  outputs: string[];
  parameters: ModuleParameter[];
};

type PipelineNode = {
  id: string;
  moduleId: string;
  x: number;
  y: number;
  parameters: Record<string, string>;
};

type PipelineConnection = {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;
};

type RunRecord = {
  id: string;
  name: string;
  status: "Queued" | "Running" | "Completed" | "Failed" | "Cancelled";
  startedAt: string;
  stdout?: string;
  stderr?: string;
  slurmJobId?: string;
  submissionError?: string;
};

type Project = {
  id: string;
  name: string;
  description: string;
  nodes: PipelineNode[];
  connections: PipelineConnection[];
  executionOrder?: string[];
  clusterConfigId: string;
  runs: RunRecord[];
};

type HPCDataFile = {
  fileName: string;
  relativePath: string;
  sizeBytes: number;
};

type ClusterConfig = {
  id: string;
  name: string;
  jobName: string;
  partition: string;
  nodes: number;
  tasks: number;
  cpus: number;
  time: string;
  memory: string;
};

const moduleDefinitions = [
  segyInDefinition as ModuleDefinition,
  segyOutDefinition as ModuleDefinition,
];

const initialClusterConfigs: ClusterConfig[] = [
  {
    id: "cluster-a100-large",
    name: "A100 Large",
    jobName: "swi_jup",
    partition: "a100serv",
    nodes: 1,
    tasks: 1,
    cpus: 36,
    time: "12:00:00",
    memory: "300G",
  },
];

const navigation: { label: Page; icon: typeof FolderKanban }[] = [
  { label: "Projects", icon: FolderKanban },
  { label: "Data Assets", icon: Database },
  { label: "Cluster Configurations", icon: Server },
  { label: "Settings", icon: Settings },
];

const makeId = () => `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

// Keep the API hostname aligned with the page hostname so SameSite=Strict cookies
// work when the frontend is opened at either localhost or 127.0.0.1.
const API_BASE_URL = `${window.location.protocol}//${window.location.hostname}:8000`;

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function readCookie(name: string): string | null {
  const prefix = `${name}=`;
  const match = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return match ? decodeURIComponent(match.slice(prefix.length)) : null;
}

async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  // The session cookie is HttpOnly. The separate CSRF cookie is intentionally
  // readable by this same-origin frontend and must be echoed in a custom header.
  if (["POST", "PUT", "PATCH", "DELETE"].includes(method) && path !== "/api/auth/login") {
    const csrfToken = readCookie("spi_csrf");
    if (csrfToken) headers.set("X-SPI-CSRF", csrfToken);
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep the HTTP status message if the response isn't JSON.
    }
    if (response.status === 401 && !["/api/auth/me", "/api/auth/login", "/api/auth/status"].includes(path)) {
      window.dispatchEvent(new Event("spi:unauthorized"));
    }
    throw new ApiError(detail, response.status);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

interface AuthUser {
  id: string;
  username: string;
}

type HPCAccessState = {
  state: "connected" | "expired" | "disconnected";
  connected: boolean;
  connectedAt: string | null;
  expiresAt: string | null;
  remainingSeconds: number;
  leaseMinutes: number;
};

function formatLeaseTime(totalSeconds: number): string {
  const seconds = Math.max(0, totalSeconds);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours > 0
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
}

async function fetchWorkspaceData(): Promise<{ projects: Project[]; configs: ClusterConfig[] }> {
  const [projects, savedConfigs] = await Promise.all([
    apiRequest<Project[]>("/api/projects"),
    apiRequest<ClusterConfig[]>("/api/cluster-configurations"),
  ]);

  let configs = savedConfigs;
  if (configs.length === 0) {
    configs = await apiRequest<ClusterConfig[]>("/api/cluster-configurations", {
      method: "PUT",
      body: JSON.stringify(initialClusterConfigs),
    });
  }
  return { projects, configs };
}

function App() {
  const [page, setPage] = useState<Page>("Projects");
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);
  const [clusterConfigs, setClusterConfigs] = useState<ClusterConfig[]>([]);
  const [notice, setNotice] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [authReady, setAuthReady] = useState(false);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authError, setAuthError] = useState("");
  const [setupRequired, setSetupRequired] = useState(false);
  const [hpcAccess, setHpcAccess] = useState<HPCAccessState | null>(null);
  const [hpcAccessBusy, setHpcAccessBusy] = useState(false);
  const [clockNow, setClockNow] = useState(Date.now());
  const saveTimersRef = useRef<Map<string, number>>(new Map());

  const showNotice = (message: string) => setNotice(message);

  const loadWorkspace = async () => {
    setIsLoading(true);
    try {
      const { projects: savedProjects, configs } = await fetchWorkspaceData();
      setProjects(savedProjects);
      setClusterConfigs(configs);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown connection error";
      setNotice(`Could not load saved SPI data: ${message}`);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    const handleUnauthorized = () => {
      setAuthUser(null);
      setProjects([]);
      setClusterConfigs([]);
      setActiveProjectId(null);
      setAuthError("Your SPI session has expired. Please sign in again.");
    };
    window.addEventListener("spi:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("spi:unauthorized", handleUnauthorized);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const restoreSession = async () => {
      try {
        const user = await apiRequest<AuthUser>("/api/auth/me");
        if (cancelled) return;
        setAuthUser(user);
        const { projects: savedProjects, configs } = await fetchWorkspaceData();
        if (cancelled) return;
        setProjects(savedProjects);
        setClusterConfigs(configs);
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          try {
            const status = await apiRequest<{ admin_configured: boolean }>("/api/auth/status");
            if (!cancelled) setSetupRequired(!status.admin_configured);
          } catch (statusError) {
            if (!cancelled) setAuthError(statusError instanceof Error ? statusError.message : "Could not contact SPI backend.");
          }
        } else if (!cancelled) {
          setAuthError(error instanceof Error ? error.message : "Could not contact SPI backend.");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
          setAuthReady(true);
        }
      }
    };
    void restoreSession();
    return () => { cancelled = true; };
  }, []);

  const refreshHpcAccess = async () => {
    const state = await apiRequest<HPCAccessState>("/api/hpc/access");
    setHpcAccess(state);
    return state;
  };

  useEffect(() => {
    if (!authUser) {
      setHpcAccess(null);
      return;
    }

    let cancelled = false;
    const pollAccess = async () => {
      try {
        const state = await apiRequest<HPCAccessState>("/api/hpc/access");
        if (!cancelled) setHpcAccess(state);
      } catch (error) {
        if (!cancelled && !(error instanceof ApiError && error.status === 401)) {
          setNotice(error instanceof Error ? error.message : "Could not read HPC access state.");
        }
      }
    };

    void pollAccess();
    const pollTimer = window.setInterval(pollAccess, 10000);
    const clockTimer = window.setInterval(() => setClockNow(Date.now()), 1000);
    return () => {
      cancelled = true;
      window.clearInterval(pollTimer);
      window.clearInterval(clockTimer);
    };
  }, [authUser?.id]);

  const handleHpcConnect = async () => {
    setHpcAccessBusy(true);

    try {
      // First request: either connects immediately or starts the key loader.
      const initial = await apiRequest<{
        state: string;
        connected: boolean;
        leaseMinutes?: number;
        message?: string;
      }>("/api/hpc/access/connect", { method: "POST" });

      let connectedState: HPCAccessState;

      if (initial.state === "awaiting_key") {
        showNotice("Waiting for SSH key. Enter your passphrase in the PowerShell window...");

        let keyLoaded = false;

        // Check the Windows SSH Agent once per second, for up to 2 minutes.
        for (let attempt = 0; attempt < 120; attempt++) {
          await new Promise(resolve => window.setTimeout(resolve, 1000));

          const keyStatus = await apiRequest<{ loaded: boolean }>(
            "/api/hpc/access/key-status"
          );

          if (keyStatus.loaded) {
            keyLoaded = true;
            break;
          }
        }

        if (!keyLoaded) {
          throw new Error(
            "SSH key was not loaded within 2 minutes. Please try connecting again."
          );
        }

        showNotice("SSH key loaded. Connecting to HPC...");

        // Exactly one automatic connection attempt after the key is detected.
        connectedState = await apiRequest<HPCAccessState>(
          "/api/hpc/access/connect",
          { method: "POST" }
        );
      } else {
        // The key was already loaded, so the first request connected directly.
        connectedState = initial as HPCAccessState;
      }

      if (!connectedState.connected) {
        throw new Error("HPC connection was not established.");
      }

      setHpcAccess(connectedState);
      showNotice(`HPC access connected for ${connectedState.leaseMinutes} minutes.`);

    } catch (error) {
      showNotice(
        error instanceof Error ? error.message : "Could not connect to HPC."
      );

      try {
        await refreshHpcAccess();
      } catch {
        // Status will be refreshed on the next poll.
      }
    } finally {
      setHpcAccessBusy(false);
    }
  };

  const handleHpcDisconnect = async () => {
    setHpcAccessBusy(true);
    try {
      const state = await apiRequest<HPCAccessState>("/api/hpc/access/disconnect", { method: "POST" });
      setHpcAccess(state);
      showNotice("HPC access disconnected. Your SPI login remains active.");
    } catch (error) {
      showNotice(error instanceof Error ? error.message : "Could not disconnect HPC access.");
    } finally {
      setHpcAccessBusy(false);
    }
  };

  const hpcSecondsRemaining = hpcAccess?.connected && hpcAccess.expiresAt
    ? Math.max(0, Math.ceil((Date.parse(hpcAccess.expiresAt) - clockNow) / 1000))
    : 0;
  const hpcLeaseActive = Boolean(hpcAccess?.connected && hpcSecondsRemaining > 0);

  const handleLogin = async (username: string, password: string) => {
    setAuthError("");
    await apiRequest<AuthUser>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const user = await apiRequest<AuthUser>("/api/auth/me");
    setAuthUser(user);
    setSetupRequired(false);
    await loadWorkspace();
  };

  const handleLogout = async () => {
    try {
      await apiRequest<void>("/api/auth/logout", { method: "POST" });
    } finally {
      setAuthUser(null);
      setProjects([]);
      setClusterConfigs([]);
      setActiveProjectId(null);
      setPage("Projects");
      setNotice("");
    }
  };

  const activeProject = projects.find((project) => project.id === activeProjectId) ?? null;

  const openProject = (projectId: string) => {
    setActiveProjectId(projectId);
    setPage("Projects");
  };

  const updateProject = (updatedProject: Project) => {
    // Update the UI immediately; persist the latest snapshot after a short pause.
    setProjects((current) =>
      current.map((project) => (project.id === updatedProject.id ? updatedProject : project)),
    );

    const existingTimer = saveTimersRef.current.get(updatedProject.id);
    if (existingTimer !== undefined) window.clearTimeout(existingTimer);

    const timer = window.setTimeout(() => {
      void apiRequest<Project>(`/api/projects/${encodeURIComponent(updatedProject.id)}`, {
        method: "PUT",
        body: JSON.stringify(updatedProject),
      }).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : "Unknown save error";
        setNotice(`Could not save project changes: ${message}`);
      }).finally(() => {
        saveTimersRef.current.delete(updatedProject.id);
      });
    }, 450);

    saveTimersRef.current.set(updatedProject.id, timer);
  };

  const createProject = async (name: string, description: string) => {
    const project: Project = {
      id: makeId(),
      name,
      description,
      nodes: [],
      connections: [],
      clusterConfigId: clusterConfigs[0]?.id ?? "",
      runs: [],
    };

    const savedProject = await apiRequest<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(project),
    });
    setProjects((current) => [...current, savedProject]);
    openProject(savedProject.id);
  };

  const updateClusterConfigs = async (updatedConfigs: ClusterConfig[]) => {
    const previousConfigs = clusterConfigs;
    setClusterConfigs(updatedConfigs);
    try {
      const savedConfigs = await apiRequest<ClusterConfig[]>("/api/cluster-configurations", {
        method: "PUT",
        body: JSON.stringify(updatedConfigs),
      });
      setClusterConfigs(savedConfigs);
    } catch (error) {
      setClusterConfigs(previousConfigs);
      const message = error instanceof Error ? error.message : "Unknown save error";
      setNotice(`Could not save cluster configurations: ${message}`);
    }
  };

  if (!authReady) {
    return (
      <div className="auth-screen">
        <div className="auth-card auth-loading">
          <div className="brand-mark"><Activity size={21} /></div>
          <h1>SPI</h1>
          <p>Checking local session…</p>
        </div>
      </div>
    );
  }

  if (!authUser) {
    return (
      <LoginPage
        onLogin={handleLogin}
        setupRequired={setupRequired}
        backendError={authError}
      />
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Activity size={21} /></div>
          <div className="brand-copy">
            <div className="brand-name">SPI</div>
            <div className="brand-subtitle">Seismic Processing Interface</div>
          </div>
        </div>

        <div className="nav-label">WORKSPACE</div>
        <nav className="side-nav">
          {navigation.map(({ label, icon: Icon }) => {
            const selected = page === label && !activeProject;
            return (
              <button
                className={`nav-item ${selected ? "active" : ""}`}
                key={label}
                onClick={() => {
                  setActiveProjectId(null);
                  setPage(label);
                }}
              >
                <Icon size={17} strokeWidth={1.8} />
                <span>{label}</span>
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div className="connection-line"><span className="status-dot" />{isLoading ? "Loading local database" : "Local database"}</div>
          <div className={`hpc-access-card ${hpcLeaseActive ? "hpc-access-connected" : ""}`}>
            <div className="hpc-access-heading">
              <span className={`status-dot ${hpcAccess?.connected ? "hpc-dot-connected" : "hpc-dot-disconnected"}`} />
              <span>HPC ACCESS</span>
            </div>
            <div className="hpc-access-state">
              {hpcLeaseActive
                ? "Connected to gpn-headnode"
                : hpcAccess?.state === "expired" || (hpcAccess?.connected && hpcSecondsRemaining === 0)
                  ? "Access lease expired"
                  : "Not connected"}
            </div>
            {hpcLeaseActive && (
              <div className="hpc-access-countdown">Expires in {formatLeaseTime(hpcSecondsRemaining)}</div>
            )}
            <button
              className={`hpc-access-button ${hpcLeaseActive ? "hpc-access-disconnect" : "hpc-access-connect"}`}
              onClick={() => void (hpcLeaseActive ? handleHpcDisconnect() : handleHpcConnect())}
              disabled={hpcAccessBusy || isLoading}
            >
              {hpcAccessBusy
                ? "Working…"
                : hpcLeaseActive
                  ? "Disconnect"
                  : hpcAccess?.state === "expired" || (hpcAccess?.connected && hpcSecondsRemaining === 0)
                    ? "Reconnect to HPC"
                    : "Connect to HPC"}
            </button>
          </div>
          <div className="sidebar-version">SPI · Local workspace</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs">
            <span>Workspace</span>
            <ChevronRight size={14} />
            {activeProject ? (
              <><span className="breadcrumb-muted">Projects</span><ChevronRight size={14} /><strong>{activeProject.name}</strong></>
            ) : <strong>{page}</strong>}
          </div>
          <div className="topbar-right">
            <span className="environment-label">LOCAL · SQLITE</span>
            <span className="auth-username">{authUser.username}</span>
            <div className="avatar">{authUser.username.slice(0, 1).toUpperCase()}</div>
            <button className="secondary-button auth-logout-button" onClick={() => void handleLogout()} title="Sign out">
              <LogOut size={14} />Sign out
            </button>
          </div>
        </header>

        <div className="page-content">
          {isLoading ? (
            <div className="placeholder-panel"><div className="empty-icon"><Database size={25} /></div><h2>Loading SPI workspace</h2><p>Reading projects and cluster configurations from the local database.</p></div>
          ) : activeProject ? (
            <ProjectWorkspace
              project={activeProject}
              clusterConfigs={clusterConfigs}
              hpcAccessConnected={hpcLeaseActive}
              onBack={() => setActiveProjectId(null)}
              onUpdate={updateProject}
              onNotice={showNotice}
            />
          ) : page === "Projects" ? (
            <ProjectsPage projects={projects} onOpen={openProject} onCreate={createProject} />
          ) : page === "Cluster Configurations" ? (
            <ClusterConfigurationsPage configs={clusterConfigs} onChange={updateClusterConfigs} />
          ) : page === "Data Assets" ? (
            <DataAssetsPage />
          ) : (
            <SettingsPage />
          )}
        </div>
      </main>

      {notice && (
        <div className="toast" role="status">
          <span>{notice}</span>
          <button aria-label="Dismiss notification" onClick={() => setNotice("")}><X size={15} /></button>
        </div>
      )}
    </div>
  );
}


function LoginPage({
  onLogin,
  setupRequired,
  backendError,
}: {
  onLogin: (username: string, password: string) => Promise<void>;
  setupRequired: boolean;
  backendError: string;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError("");
    try {
      await onLogin(username, password);
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Sign-in failed.");
    } finally {
      setSubmitting(false);
      setPassword("");
    }
  };

  return (
    <div className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <div className="brand-mark"><Activity size={22} /></div>
          <div><div className="brand-name">SPI</div><div className="brand-subtitle">Seismic Processing Interface</div></div>
        </div>
        {setupRequired ? (
          <div className="auth-setup-message">
            <div className="auth-icon"><KeyRound size={22} /></div>
            <h1>Set up your administrator</h1>
            <p>No SPI account exists yet. Create the single local administrator account from your backend PowerShell terminal:</p>
            <pre>python -m app.cli create-admin</pre>
            <p className="auth-muted">The password is entered interactively and stored as an Argon2id hash. There is no public registration page.</p>
          </div>
        ) : (
          <>
            <div className="auth-heading"><h1>Welcome back</h1><p>Sign in to your local SPI workspace.</p></div>
            <form className="auth-form" onSubmit={submit}>
              <div className="form-field">
                <label htmlFor="spi-username">Username</label>
                <input id="spi-username" name="username" autoComplete="username" required maxLength={64} value={username} onChange={(event) => setUsername(event.target.value)} autoFocus />
              </div>
              <div className="form-field">
                <label htmlFor="spi-password">Password</label>
                <input id="spi-password" name="password" type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} />
              </div>
              {(error || backendError) && <div className="auth-error" role="alert">{error || backendError}</div>}
              <button className="primary-button auth-submit" type="submit" disabled={submitting || !username.trim() || !password}>
                <KeyRound size={15} />{submitting ? "Signing in…" : "Sign in"}
              </button>
            </form>
            <p className="auth-footer-note">Single-user local installation · Registration is disabled</p>
          </>
        )}
        {backendError && setupRequired && <div className="auth-error" role="alert">{backendError}</div>}
      </div>
    </div>
  );
}

function ProjectsPage({
  projects,
  onOpen,
  onCreate,
}: {
  projects: Project[];
  onOpen: (id: string) => void;
  onCreate: (name: string, description: string) => Promise<void>;
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createError, setCreateError] = useState("");
  const [creating, setCreating] = useState(false);

  const createProject = async () => {
    if (!name.trim() || creating) return;
    setCreating(true);
    setCreateError("");
    try {
      await onCreate(name.trim(), description.trim());
      setName("");
      setDescription("");
      setCreateOpen(false);
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : "Could not create project.");
    } finally {
      setCreating(false);
    }
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">WORKSPACE</div>
          <h1>Projects</h1>
          <p className="page-description">Create a project or continue working on an existing seismic workflow.</p>
        </div>
        <button className="primary-button" onClick={() => setCreateOpen(true)}><Plus size={16} />New project</button>
      </div>

      {projects.length === 0 ? (
        <div className="empty-projects">
          <div className="empty-icon"><FolderKanban size={27} /></div>
          <h2>No projects yet</h2>
          <p>Projects contain your pipeline workspace, module configurations, and execution history.</p>
          <button className="primary-button" onClick={() => setCreateOpen(true)}><Plus size={16} />Create your first project</button>
        </div>
      ) : (
        <div className="project-grid">
          {projects.map((project) => (
            <button className="project-card" key={project.id} onClick={() => onOpen(project.id)}>
              <div className="project-card-top"><div className="project-card-icon"><FolderKanban size={19} /></div><ChevronRight size={17} className="muted-icon" /></div>
              <h2>{project.name}</h2>
              <p>{project.description || "No description provided."}</p>
              <div className="project-card-meta"><span>{project.nodes.length} modules</span><span>{project.runs.length} runs</span></div>
            </button>
          ))}
        </div>
      )}

      {createOpen && (
        <Modal title="Create project" eyebrow="NEW PROJECT" onClose={() => setCreateOpen(false)}>
          <div className="form-field">
            <label htmlFor="project-name">Project name <span className="required">*</span></label>
            <input id="project-name" autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. North Sea Survey" onKeyDown={(event) => { if (event.key === "Enter") createProject(); }} />
          </div>
          <div className="form-field">
            <label htmlFor="project-description">Description</label>
            <textarea id="project-description" rows={3} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Optional project description" />
          </div>
          {createError && <div className="form-error">{createError}</div>}
          <div className="modal-actions">
            <button className="secondary-button" onClick={() => setCreateOpen(false)}>Cancel</button>
            <button className="primary-button" disabled={!name.trim() || creating} onClick={() => void createProject()}>{creating ? "Creating…" : "Create project"}</button>
          </div>
        </Modal>
      )}
    </>
  );
}

const PIPELINE_NODE_WIDTH = 205;
const PIPELINE_NODE_HEIGHT = 78;

type GraphPoint = { x: number; y: number };
type GraphInteraction =
  | {
      kind: "move";
      nodeId: string;
      offsetX: number;
      offsetY: number;
      pointerStart: GraphPoint;
      moved: boolean;
    }
  | { kind: "connect"; sourceNodeId: string };

type DraftConnection = { sourceNodeId: string; pointer: GraphPoint };
type GraphContextMenu = {
  kind: "node" | "connection";
  id: string;
  x: number;
  y: number;
};

function nodeCenter(node: PipelineNode): GraphPoint {
  return {
    x: node.x + PIPELINE_NODE_WIDTH / 2,
    y: node.y + PIPELINE_NODE_HEIGHT / 2,
  };
}

// Finds where a ray from a node centre intersects its rectangular outline.
function nodeOutlinePoint(node: PipelineNode, toward: GraphPoint): GraphPoint {
  const center = nodeCenter(node);
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  if (dx === 0 && dy === 0) return center;

  const scaleX = dx === 0 ? Number.POSITIVE_INFINITY : (PIPELINE_NODE_WIDTH / 2) / Math.abs(dx);
  const scaleY = dy === 0 ? Number.POSITIVE_INFINITY : (PIPELINE_NODE_HEIGHT / 2) / Math.abs(dy);
  const scale = Math.min(scaleX, scaleY);

  return { x: center.x + dx * scale, y: center.y + dy * scale };
}

// The tangent points out through whichever side of the bubble the line reaches.
function nodeOutlineNormal(node: PipelineNode, toward: GraphPoint): GraphPoint {
  const center = nodeCenter(node);
  const dx = toward.x - center.x;
  const dy = toward.y - center.y;
  const horizontalRatio = Math.abs(dx) / (PIPELINE_NODE_WIDTH / 2);
  const verticalRatio = Math.abs(dy) / (PIPELINE_NODE_HEIGHT / 2);

  if (horizontalRatio >= verticalRatio) {
    return { x: dx < 0 ? -1 : 1, y: 0 };
  }
  return { x: 0, y: dy < 0 ? -1 : 1 };
}

// Curve handles follow the actual departure and arrival sides instead of assuming
// that every connection travels from left to right.
function connectionPath(
  start: GraphPoint,
  end: GraphPoint,
  startNormal: GraphPoint,
  endNormal: GraphPoint,
): string {
  const distance = Math.hypot(end.x - start.x, end.y - start.y);
  const handle = Math.min(132, distance * 0.4);
  const control1 = {
    x: start.x + startNormal.x * handle,
    y: start.y + startNormal.y * handle,
  };
  const control2 = {
    x: end.x + endNormal.x * handle,
    y: end.y + endNormal.y * handle,
  };
  return `M ${start.x} ${start.y} C ${control1.x} ${control1.y}, ${control2.x} ${control2.y}, ${end.x} ${end.y}`;
}

function nodeConnectionPath(source: PipelineNode, target: PipelineNode): string {
  const sourceCenter = nodeCenter(source);
  const targetCenter = nodeCenter(target);
  const start = nodeOutlinePoint(source, targetCenter);
  const finish = nodeOutlinePoint(target, sourceCenter);
  return connectionPath(
    start,
    finish,
    nodeOutlineNormal(source, targetCenter),
    nodeOutlineNormal(target, sourceCenter),
  );
}

function draftConnectionPath(source: PipelineNode, pointer: GraphPoint): string {
  const start = nodeOutlinePoint(source, pointer);
  const towardPointer = nodeOutlineNormal(source, pointer);
  const dx = start.x - pointer.x;
  const dy = start.y - pointer.y;
  const length = Math.hypot(dx, dy) || 1;
  const endNormal = { x: dx / length, y: dy / length };
  return connectionPath(start, pointer, towardPointer, endNormal);
}

function wouldCreateCycle(
  connections: PipelineConnection[],
  sourceNodeId: string,
  targetNodeId: string,
): boolean {
  // Adding source -> target is invalid if target can already reach source.
  const pending = [targetNodeId];
  const visited = new Set<string>();

  while (pending.length > 0) {
    const current = pending.pop()!;
    if (current === sourceNodeId) return true;
    if (visited.has(current)) continue;
    visited.add(current);

    for (const connection of connections) {
      if (connection.sourceNodeId === current) {
        pending.push(connection.targetNodeId);
      }
    }
  }

  return false;
}

// Keep this algorithm in sync with backend/app/services/pipeline_order.py.
// It gives every module a stable, dependency-respecting display number.
function getOperationOrder(
  nodes: PipelineNode[],
  connections: PipelineConnection[],
): Map<string, number> {
  const indegree = new Map<string, number>();
  const outgoing = new Map<string, string[]>();

  for (const node of nodes) {
    indegree.set(node.id, 0);
    outgoing.set(node.id, []);
  }

  for (const connection of connections) {
    if (!indegree.has(connection.sourceNodeId) || !indegree.has(connection.targetNodeId)) {
      continue;
    }
    outgoing.get(connection.sourceNodeId)!.push(connection.targetNodeId);
    indegree.set(
      connection.targetNodeId,
      indegree.get(connection.targetNodeId)! + 1,
    );
  }

  // Initial ready nodes and newly ready nodes keep their stable insertion order.
  const ready = nodes
    .filter((node) => indegree.get(node.id) === 0)
    .map((node) => node.id);
  const orderedIds: string[] = [];

  for (let cursor = 0; cursor < ready.length; cursor += 1) {
    const current = ready[cursor];
    orderedIds.push(current);

    for (const target of outgoing.get(current) ?? []) {
      const remaining = indegree.get(target)! - 1;
      indegree.set(target, remaining);
      if (remaining === 0) ready.push(target);
    }
  }

  // A valid project is acyclic (the API enforces this when saving). Keep a
  // deterministic display fallback in case an older/corrupt snapshot is loaded.
  const orderedSet = new Set(orderedIds);
  for (const node of nodes) {
    if (!orderedSet.has(node.id)) orderedIds.push(node.id);
  }

  return new Map(orderedIds.map((id, index) => [id, index + 1]));
}

function ProjectWorkspace({
  project,
  clusterConfigs,
  hpcAccessConnected,
  onBack,
  onUpdate,
  onNotice,
}: {
  project: Project;
  clusterConfigs: ClusterConfig[];
  hpcAccessConnected: boolean;
  onBack: () => void;
  onUpdate: (project: Project) => void;
  onNotice: (message: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedConnectionId, setSelectedConnectionId] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<GraphContextMenu | null>(null);
  const [editingNodeId, setEditingNodeId] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [runSubmitting, setRunSubmitting] = useState(false);
  const [draftConnection, setDraftConnection] = useState<DraftConnection | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const interactionRef = useRef<GraphInteraction | null>(null);

  // Fallback keeps older in-memory project objects usable if they predate connections.
  const connections = project.connections ?? [];
  const operationOrder = useMemo(
    () => getOperationOrder(project.nodes, connections),
    [project.nodes, connections],
  );

  const filteredModules = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return moduleDefinitions;
    return moduleDefinitions.filter((module) =>
      `${module.name} ${module.description} ${module.id}`.toLowerCase().includes(term),
    );
  }, [search]);

  const selectedNode = project.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const editingNode = project.nodes.find((node) => node.id === editingNodeId) ?? null;
  const editingModule = editingNode ? moduleDefinitions.find((module) => module.id === editingNode.moduleId) ?? null : null;
  const selectedCluster = clusterConfigs.find((config) => config.id === project.clusterConfigId) ?? null;
  const latestRun = project.runs[0] ?? null;

  const launchRunAll = async () => {
    if (!hpcAccessConnected) {
      onNotice("Connect to HPC before submitting a pipeline.");
      return;
    }
    if (runSubmitting) return;
    setRunSubmitting(true);
    try {
      // Flush the latest debounced graph snapshot before the backend reads SQLite for Run All.
      await apiRequest<Project>(`/api/projects/${encodeURIComponent(project.id)}`, {
        method: "PUT",
        body: JSON.stringify(project),
      });
      const run = await apiRequest<RunRecord>(
        `/api/projects/${encodeURIComponent(project.id)}/runs`,
        { method: "POST" },
      );
      onUpdate({ ...project, runs: [run, ...project.runs.filter((item) => item.id !== run.id)] });
      setHistoryOpen(true);
      onNotice(`Run submitted to SLURM. Job ID: ${run.slurmJobId ?? "pending"}.`);
    } catch (error) {
      // A submission error is also recorded in the backend run history when a run row exists.
      try {
        const refreshed = await apiRequest<Project>(`/api/projects/${encodeURIComponent(project.id)}`);
        onUpdate(refreshed);
      } catch {
        // Keep the original submission error as the visible message.
      }
      onNotice(error instanceof Error ? error.message : "Could not submit Run All.");
    } finally {
      setRunSubmitting(false);
    }
  };

  const clearRunHistory = async () => {
    try {
      await apiRequest<void>(`/api/projects/${encodeURIComponent(project.id)}/runs`, { method: "DELETE" });
      onUpdate({ ...project, runs: [] });
    } catch (error) {
      onNotice(error instanceof Error ? error.message : "Could not clear run history.");
    }
  };

  const addModule = (module: ModuleDefinition) => {
    const canvasWidth = canvasRef.current?.clientWidth ?? 980;
    const columns = Math.max(1, Math.floor((canvasWidth - 48) / 245));
    const column = project.nodes.length % columns;
    const row = Math.floor(project.nodes.length / columns);
    const node: PipelineNode = {
      id: makeId(),
      moduleId: module.id,
      x: 24 + column * 245,
      y: 40 + row * 145,
      parameters: {},
    };
    onUpdate({ ...project, nodes: [...project.nodes, node], connections });
    setSelectedNodeId(node.id);
    setSearch("");
    setSuggestionsOpen(false);
  };

  const handleSearchKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!suggestionsOpen || filteredModules.length === 0) {
      if (event.key === "Enter" && filteredModules.length > 0) addModule(filteredModules[0]);
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightedIndex((index) => (index + 1) % filteredModules.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightedIndex((index) => (index - 1 + filteredModules.length) % filteredModules.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      addModule(filteredModules[highlightedIndex] ?? filteredModules[0]);
    } else if (event.key === "Escape") {
      setSuggestionsOpen(false);
    }
  };

  const canvasPoint = (event: ReactPointerEvent<HTMLDivElement>): GraphPoint => {
    const bounds = canvasRef.current!.getBoundingClientRect();
    return {
      x: event.clientX - bounds.left + canvasRef.current!.scrollLeft,
      y: event.clientY - bounds.top + canvasRef.current!.scrollTop,
    };
  };

  const showGraphContextMenu = (kind: GraphContextMenu["kind"], id: string, point: GraphPoint) => {
    const canvas = canvasRef.current;
    const menuWidth = 176;
    const menuHeight = kind === "node" ? 104 : 70;
    const minX = (canvas?.scrollLeft ?? 0) + 8;
    const minY = (canvas?.scrollTop ?? 0) + 8;
    const maxX = Math.max(minX, (canvas?.scrollLeft ?? 0) + (canvas?.clientWidth ?? 900) - menuWidth - 8);
    const maxY = Math.max(minY, (canvas?.scrollTop ?? 0) + (canvas?.clientHeight ?? 500) - menuHeight - 8);
    setContextMenu({
      kind,
      id,
      x: Math.max(minX, Math.min(point.x, maxX)),
      y: Math.max(minY, Math.min(point.y, maxY)),
    });
  };

  const removeNodeById = (nodeId: string) => {
    onUpdate({
      ...project,
      nodes: project.nodes.filter((node) => node.id !== nodeId),
      connections: connections.filter((connection) =>
        connection.sourceNodeId !== nodeId && connection.targetNodeId !== nodeId,
      ),
    });
    if (selectedNodeId === nodeId) setSelectedNodeId(null);
    setContextMenu(null);
  };

  const removeConnectionById = (connectionId: string) => {
    onUpdate({
      ...project,
      connections: connections.filter((connection) => connection.id !== connectionId),
    });
    if (selectedConnectionId === connectionId) setSelectedConnectionId(null);
    setContextMenu(null);
  };

  const editNodeById = (nodeId: string) => {
    setEditingNodeId(nodeId);
    setSelectedNodeId(nodeId);
    setSelectedConnectionId(null);
    setContextMenu(null);
  };

  const handleCanvasKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest(".graph-context-menu")) return;

    if (event.key === "Escape") {
      setContextMenu(null);
      return;
    }

    if (event.key === "Delete" || event.key === "Backspace") {
      if (selectedNodeId) {
        event.preventDefault();
        removeNodeById(selectedNodeId);
      } else if (selectedConnectionId) {
        event.preventDefault();
        removeConnectionById(selectedConnectionId);
      }
      return;
    }

    if (event.key === "Enter" && selectedNodeId) {
      event.preventDefault();
      editNodeById(selectedNodeId);
    }
  };

  const handleProjectPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const target = event.target as Element;
    if (target.closest(".graph-context-menu")) return;
    if (target.closest(".pipeline-node, .pipeline-connection-hitarea")) return;
    setContextMenu(null);
  };

  const moveNode = (nodeId: string, x: number, y: number) => {
    onUpdate({
      ...project,
      nodes: project.nodes.map((node) => node.id === nodeId ? { ...node, x, y } : node),
      connections,
    });
  };

  const handleCanvasPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    const target = event.target as Element;
    if (target.closest(".graph-context-menu")) return;

    const point = canvasPoint(event);
    const connectionPathElement = target.closest<SVGPathElement>(".pipeline-connection-hitarea");
    if (connectionPathElement) {
      const connectionId = connectionPathElement.dataset.connectionId;
      if (connectionId) {
        interactionRef.current = null;
        setDraftConnection(null);
        setSelectedNodeId(null);
        setSelectedConnectionId(connectionId);
        showGraphContextMenu("connection", connectionId, point);
        canvasRef.current?.focus({ preventScroll: true });
      }
      return;
    }

    const connector = target.closest<HTMLButtonElement>(".node-connector");
    if (connector) {
      const sourceNodeId = connector.dataset.nodeId;
      if (!sourceNodeId) return;
      setContextMenu(null);
      setSelectedNodeId(null);
      setSelectedConnectionId(null);
      interactionRef.current = { kind: "connect", sourceNodeId };
      setDraftConnection({ sourceNodeId, pointer: point });
      canvasRef.current?.setPointerCapture(event.pointerId);
      return;
    }

    const nodeElement = target.closest<HTMLElement>(".pipeline-node");
    if (nodeElement) {
      const nodeId = nodeElement.dataset.nodeId;
      const node = project.nodes.find((item) => item.id === nodeId);
      if (!nodeId || !node) return;
      setContextMenu(null);
      setSelectedNodeId(nodeId);
      setSelectedConnectionId(null);
      interactionRef.current = {
        kind: "move",
        nodeId,
        offsetX: point.x - node.x,
        offsetY: point.y - node.y,
        pointerStart: point,
        moved: false,
      };
      canvasRef.current?.focus({ preventScroll: true });
      canvasRef.current?.setPointerCapture(event.pointerId);
      return;
    }

    interactionRef.current = null;
    setContextMenu(null);
    setSelectedNodeId(null);
    setSelectedConnectionId(null);
  };

  const handleCanvasPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const interaction = interactionRef.current;
    if (!interaction) return;
    const point = canvasPoint(event);

    if (interaction.kind === "connect") {
      setDraftConnection({ sourceNodeId: interaction.sourceNodeId, pointer: point });
      return;
    }

    const movement = Math.hypot(
      point.x - interaction.pointerStart.x,
      point.y - interaction.pointerStart.y,
    );
    if (!interaction.moved && movement > 4) {
      interaction.moved = true;
      setContextMenu(null);
    }

    if (interaction.moved) {
      moveNode(
        interaction.nodeId,
        Math.max(12, point.x - interaction.offsetX),
        Math.max(12, point.y - interaction.offsetY),
      );
    }
  };

  const handleCanvasPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const interaction = interactionRef.current;
    const point = canvasPoint(event);

    if (interaction?.kind === "connect") {
      const target = [...project.nodes].reverse().find((node) =>
        node.id !== interaction.sourceNodeId &&
        point.x >= node.x && point.x <= node.x + PIPELINE_NODE_WIDTH &&
        point.y >= node.y && point.y <= node.y + PIPELINE_NODE_HEIGHT,
      );

      if (target) {
        const alreadyConnected = connections.some((connection) =>
          connection.sourceNodeId === interaction.sourceNodeId &&
          connection.targetNodeId === target.id,
        );
        if (!alreadyConnected && wouldCreateCycle(connections, interaction.sourceNodeId, target.id)) {
          onNotice("That connection would create a cycle. Pipeline execution must flow without loops.");
        } else if (!alreadyConnected) {
          onUpdate({
            ...project,
            connections: [
              ...connections,
              { id: makeId(), sourceNodeId: interaction.sourceNodeId, targetNodeId: target.id },
            ],
          });
        }
      }
    } else if (interaction?.kind === "move" && !interaction.moved) {
      // A pointer press and release without a real drag is a node selection.
      // The small action menu opens beside the selected bubble.
      showGraphContextMenu("node", interaction.nodeId, point);
    }

    interactionRef.current = null;
    setDraftConnection(null);
    const canvas = canvasRef.current;
    if (canvas?.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  };

  const handleCanvasPointerCancel = (event: ReactPointerEvent<HTMLDivElement>) => {
    interactionRef.current = null;
    setDraftConnection(null);
    const canvas = canvasRef.current;
    if (canvas?.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
  };

  const saveParameters = (nodeId: string, parameters: Record<string, string>) => {
    onUpdate({
      ...project,
      nodes: project.nodes.map((node) => node.id === nodeId ? { ...node, parameters } : node),
      connections,
    });
    setEditingNodeId(null);
    setSelectedNodeId(nodeId);
  };

  const removeSelectedNode = () => {
    if (!selectedNode) return;
    removeNodeById(selectedNode.id);
  };

  const updateCluster = (clusterConfigId: string) => onUpdate({ ...project, clusterConfigId, connections });

  const rightEdge = Math.max(
    canvasRef.current?.clientWidth ?? 0,
    ...project.nodes.map((node) => node.x + PIPELINE_NODE_WIDTH + 64),
    800,
  );
  const bottomEdge = Math.max(
    canvasRef.current?.clientHeight ?? 0,
    ...project.nodes.map((node) => node.y + PIPELINE_NODE_HEIGHT + 64),
    480,
  );

  return (
    <div className="project-environment" onPointerDown={handleProjectPointerDown}>
      <div className="project-workspace-header">
        <div className="project-heading-left">
          <button className="back-link" onClick={onBack}><ArrowLeft size={15} />Projects</button>
          <div className="project-name-line"><h1>{project.name}</h1><span className="module-count">{project.nodes.length} modules</span></div>
          {project.description && <p className="project-description">{project.description}</p>}
        </div>
        <div className="project-header-actions">
          <button className="secondary-button header-button" onClick={() => setHistoryOpen(true)}><History size={15} />Run history</button>
          <button
            className="primary-button"
            onClick={() => void launchRunAll()}
            disabled={runSubmitting || !hpcAccessConnected}
            title={!hpcAccessConnected ? "Connect to HPC to submit this pipeline" : "Submit the complete pipeline to SLURM"}
          >
            <Play size={15} fill="currentColor" />{runSubmitting ? "Submitting…" : "Run All"}
          </button>
        </div>
      </div>

      <div className="module-search-wrap">
        <div className={`module-search ${suggestionsOpen ? "search-focused" : ""}`}>
          <Search size={17} />
          <input
            value={search}
            onChange={(event) => { setSearch(event.target.value); setSuggestionsOpen(true); setHighlightedIndex(0); }}
            onFocus={() => setSuggestionsOpen(true)}
            onKeyDown={handleSearchKeyDown}
            onBlur={() => window.setTimeout(() => setSuggestionsOpen(false), 140)}
            placeholder="Search modules to add to this pipeline..."
            aria-label="Search modules"
            aria-expanded={suggestionsOpen}
          />
          <kbd>↑↓</kbd><kbd>ENTER</kbd>
        </div>
        {suggestionsOpen && (
          <div className="module-suggestions">
            <div className="suggestion-heading">{search.trim() ? "MATCHING MODULES" : "AVAILABLE MODULES"}</div>
            {filteredModules.length ? filteredModules.map((module, index) => (
              <button
                key={module.id}
                className={`suggestion-item ${index === highlightedIndex ? "highlighted" : ""}`}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setHighlightedIndex(index)}
                onClick={() => addModule(module)}
              >
                <span className="suggestion-icon">{module.id === "segyin" ? <FileInput size={17} /> : <FileOutput size={17} />}</span>
                <span className="suggestion-copy"><strong>{module.name}</strong><small>{module.description}</small></span>
                <span className="suggestion-category">{module.category}</span>
                <ArrowRight size={15} className="suggestion-arrow" />
              </button>
            )) : <div className="no-suggestions">No matching modules.</div>}
          </div>
        )}
      </div>

      <div className="canvas-heading">
        <span>PIPELINE WORKSPACE</span>
        <span>Select a bubble for actions · Enter to edit · Delete to remove · Drag connector dots to connect</span>
      </div>
      <div
        className="pipeline-canvas"
        ref={canvasRef}
        tabIndex={0}
        aria-label="Pipeline graph. Select a module to open its actions. Use Enter to edit and Delete to remove."
        onKeyDown={handleCanvasKeyDown}
        onPointerDown={handleCanvasPointerDown}
        onPointerMove={handleCanvasPointerMove}
        onPointerUp={handleCanvasPointerUp}
        onPointerCancel={handleCanvasPointerCancel}
      >
        <svg
          className="pipeline-connections"
          width={rightEdge}
          height={bottomEdge}
          viewBox={`0 0 ${rightEdge} ${bottomEdge}`}
          aria-hidden="true"
        >
          <defs>
            <marker id="pipeline-connection-arrow" markerWidth="9" markerHeight="9" refX="7.5" refY="4" orient="auto" markerUnits="strokeWidth">
              <path d="M0,0 L8,4 L0,8 Z" />
            </marker>
          </defs>
          {connections.map((connection) => {
            const source = project.nodes.find((node) => node.id === connection.sourceNodeId);
            const target = project.nodes.find((node) => node.id === connection.targetNodeId);
            if (!source || !target) return null;
            const path = nodeConnectionPath(source, target);
            const selected = selectedConnectionId === connection.id;
            return (
              <g key={connection.id}>
                <path
                  className="pipeline-connection-hitarea"
                  data-connection-id={connection.id}
                  d={path}
                />
                <path
                  className={`pipeline-connection ${selected ? "connection-selected" : ""}`}
                  d={path}
                  markerEnd="url(#pipeline-connection-arrow)"
                />
              </g>
            );
          })}
          {draftConnection && (() => {
            const source = project.nodes.find((node) => node.id === draftConnection.sourceNodeId);
            if (!source) return null;
            return <path className="pipeline-connection pipeline-connection-draft" d={draftConnectionPath(source, draftConnection.pointer)} />;
          })()}
        </svg>

        {project.nodes.length === 0 ? (
          <div className="canvas-empty-state">
            <div className="canvas-empty-icon"><Workflow size={25} /></div>
            <h2>Your pipeline starts here</h2>
            <p>Search for a module above and select it to add it to this workspace.</p>
            <div className="canvas-hint"><Search size={13} />Try searching for <strong>SEGYIN</strong></div>
          </div>
        ) : project.nodes.map((node, index) => {
          const module = moduleDefinitions.find((item) => item.id === node.moduleId);
          if (!module) return null;
          const configured = module.parameters.every((parameter) => !parameter.required || Boolean(node.parameters[parameter.name]?.trim()));
          const selected = selectedNodeId === node.id;
          return (
            <div
              key={node.id}
              data-node-id={node.id}
              className={`pipeline-node ${selected ? "node-selected" : ""}`}
              style={{ left: node.x, top: node.y }}
              onDoubleClick={(event) => { event.stopPropagation(); editNodeById(node.id); }}
              title="Click for actions · Drag to move · Double-click or press Enter to edit"
            >
              <div className="node-index">{String(operationOrder.get(node.id) ?? index + 1).padStart(2, "0")}</div>
              <div className="node-icon">{module.id === "segyin" ? <FileInput size={19} /> : <FileOutput size={19} />}</div>
              <div className="node-main"><strong>{module.name}</strong><span>{configured ? "Configured" : "Needs configuration"}</span></div>
              <span className={`node-status ${configured ? "is-configured" : "is-pending"}`} />
              <button
                type="button"
                className="node-connector"
                data-node-id={node.id}
                aria-label={`Start a connection from ${module.name}`}
                title="Hold and drag to another module"
                onClick={(event) => event.stopPropagation()}
              />
            </div>
          );
        })}

        {contextMenu && (
          <div
            className="graph-context-menu"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <div className="graph-context-menu-heading">
              {contextMenu.kind === "node" ? "MODULE ACTIONS" : "CONNECTION ACTIONS"}
            </div>
            {contextMenu.kind === "node" ? (
              <>
                <button
                  type="button"
                  className="graph-context-menu-item"
                  onClick={() => editNodeById(contextMenu.id)}
                >
                  <Settings size={14} />Edit module <span>Enter</span>
                </button>
                <button
                  type="button"
                  className="graph-context-menu-item graph-context-menu-danger"
                  onClick={() => removeNodeById(contextMenu.id)}
                >
                  <Trash2 size={14} />Delete module <span>Del</span>
                </button>
              </>
            ) : (
              <button
                type="button"
                className="graph-context-menu-item graph-context-menu-danger"
                onClick={() => removeConnectionById(contextMenu.id)}
              >
                <Trash2 size={14} />Delete connection
              </button>
            )}
          </div>
        )}
      </div>

      {selectedNode && (
        <div className="selection-bar">
          <div className="selection-info">
            <div className="selection-icon">{selectedNode.moduleId === "segyin" ? <FileInput size={17} /> : <FileOutput size={17} />}</div>
            <div><strong>{moduleDefinitions.find((module) => module.id === selectedNode.moduleId)?.name}</strong><span>Selected module</span></div>
          </div>
          <div className="selection-controls">
            <button className="secondary-button compact-button" onClick={() => setEditingNodeId(selectedNode.id)}>Configure</button>
            <button className="secondary-button compact-button" onClick={() => onNotice("Run Cell is a UI placeholder. Execution and upstream dependency handling will be connected next.")}><Play size={14} />Run Cell</button>
            <button className="icon-button danger-hover" aria-label="Remove module" title="Remove module" onClick={removeSelectedNode}><Trash2 size={16} /></button>
          </div>
        </div>
      )}

      <div className="workspace-footer">
        <div className="footer-left">
          <span><Layers size={13} />{project.nodes.length} modules in pipeline</span>
          <span><Workflow size={13} />{connections.length} connections</span>
          <span><Clock3 size={13} />{latestRun ? `Latest run: ${latestRun.status}` : "Execution not started"}</span>
        </div>
        <label className="cluster-picker"><Server size={14} /><span>Cluster</span>
          <select value={project.clusterConfigId} onChange={(event) => updateCluster(event.target.value)}>
            <option value="">Select configuration</option>
            {clusterConfigs.map((config) => <option key={config.id} value={config.id}>{config.name}</option>)}
          </select>
          <ChevronDown size={13} />
        </label>
        {selectedCluster && <span className="cluster-summary">{selectedCluster.partition} · {selectedCluster.cpus} CPUs · {selectedCluster.memory}</span>}
      </div>

      {editingNode && editingModule && (
        <ModuleParameterModal
          module={editingModule}
          initialParameters={editingNode.parameters}
          onClose={() => setEditingNodeId(null)}
          onSave={(parameters) => saveParameters(editingNode.id, parameters)}
        />
      )}

      {historyOpen && <RunHistoryPanel projectId={project.id} runs={project.runs} onClose={() => setHistoryOpen(false)} onClear={clearRunHistory} />}
    </div>
  );
}

function ModuleParameterModal({
  module,
  initialParameters,
  onClose,
  onSave,
}: {
  module: ModuleDefinition;
  initialParameters: Record<string, string>;
  onClose: () => void;
  onSave: (parameters: Record<string, string>) => void;
}) {
  const [parameters, setParameters] = useState<Record<string, string>>(initialParameters);
  const [error, setError] = useState("");
  const [availableFiles, setAvailableFiles] = useState<HPCDataFile[]>([]);
  const [filesLoading, setFilesLoading] = useState(false);
  const [fileLoadError, setFileLoadError] = useState("");

  const hasDataAssetParameter = module.parameters.some((parameter) => parameter.type === "data_asset");
  const normalizedModuleId = module.id.toLowerCase().replace(/[_-]/g, "");
  const isSegyOut = normalizedModuleId === "segyout";
  const shouldLoadStorageFiles = hasDataAssetParameter || isSegyOut;

  useEffect(() => {
    if (!shouldLoadStorageFiles) return;
    let cancelled = false;
    setFilesLoading(true);
    setFileLoadError("");
    apiRequest<{ directory: string; files: HPCDataFile[] }>("/api/data-assets/files")
      .then((result) => {
        if (!cancelled) setAvailableFiles(result.files);
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setFileLoadError(loadError instanceof Error ? loadError.message : "Could not load HPC files.");
        }
      })
      .finally(() => {
        if (!cancelled) setFilesLoading(false);
      });
    return () => { cancelled = true; };
  }, [shouldLoadStorageFiles, module.id]);

  const pseudocode = `${module.name}(\n${module.parameters.map((parameter) => `    ${parameter.name} = ${parameters[parameter.name]?.trim() ? JSON.stringify(parameters[parameter.name]) : "<" + parameter.type + ">"}`).join(",\n")}\n)`;

  const setParameter = (name: string, value: string) => {
    setParameters((current) => ({ ...current, [name]: value }));
    setError("");
  };

  const displayOutputFilename = (storedValue: string) => storedValue.replace(/^storage\/data\//, "");

  const save = () => {
    const missing = module.parameters.find((parameter) => parameter.required && !parameters[parameter.name]?.trim());
    if (missing) {
      setError(`Please provide ${missing.label.toLowerCase()}.`);
      return;
    }

    if (isSegyOut) {
      const outputParameter = module.parameters.find((parameter) => parameter.name === "path");
      const storedValue = outputParameter ? parameters[outputParameter.name] ?? "" : "";
      const filename = displayOutputFilename(storedValue).trim();
      if (!filename || filename === "." || filename === ".." || /[\\/]/.test(filename)) {
        setError("Enter a filename only. Directory paths are not allowed.");
        return;
      }
      if (!/\.(sgy|segy)$/i.test(filename)) {
        setError("The output filename must end in .sgy or .segy.");
        return;
      }
      if (outputParameter) {
        setParameters((current) => ({ ...current, [outputParameter.name]: `storage/data/${filename}` }));
        onSave({ ...parameters, [outputParameter.name]: `storage/data/${filename}` });
        return;
      }
    }

    onSave(parameters);
  };

  return (
    <Modal title={`Configure ${module.name}`} eyebrow="MODULE PARAMETERS" onClose={onClose} wide>
      <p className="modal-intro">Set the parameters for this module. SEG-Y input and output are restricted to the SPI data directory.</p>
      <div className="parameter-layout">
        <div className="parameter-form">
          {module.parameters.map((parameter) => {
            const isDataAsset = parameter.type === "data_asset";
            const isOutputFilename = isSegyOut && parameter.name === "path";
            const currentValue = parameters[parameter.name] ?? "";
            const visibleOutputName = displayOutputFilename(currentValue);
            return (
              <div className="form-field" key={parameter.name}>
                <label htmlFor={`parameter-${parameter.name}`}>
                  {isOutputFilename ? "Output SEG-Y filename" : parameter.label}
                  {parameter.required && <span className="required"> *</span>}
                </label>
                {isDataAsset ? (
                  <>
                    <select
                      id={`parameter-${parameter.name}`}
                      value={currentValue}
                      onChange={(event) => setParameter(parameter.name, event.target.value)}
                      disabled={filesLoading}
                    >
                      <option value="">{filesLoading ? "Loading files from HPC..." : "Select a SEG-Y file"}</option>
                      {currentValue && !availableFiles.some((file) => file.relativePath === currentValue) && (
                        <option value={currentValue}>{currentValue.replace(/^storage\/data\//, "")} (not currently listed)</option>
                      )}
                      {availableFiles.map((file) => (
                        <option key={file.relativePath} value={file.relativePath}>{file.fileName}</option>
                      ))}
                    </select>
                    {fileLoadError && <small className="form-error">{fileLoadError}</small>}
                    {!fileLoadError && <small>Choose a file from the fixed HPC directory: storage/data/</small>}
                  </>
                ) : isOutputFilename ? (
                  <>
                    <input
                      id={`parameter-${parameter.name}`}
                      value={visibleOutputName}
                      onChange={(event) => setParameter(parameter.name, event.target.value ? `storage/data/${event.target.value}` : "")}
                      placeholder="processed_output.sgy"
                      autoComplete="off"
                    />
                    {fileLoadError && <small className="form-error">Could not check existing filenames: {fileLoadError}</small>}
                    {!fileLoadError && availableFiles.some((file) => file.fileName.toLowerCase() === visibleOutputName.toLowerCase()) && (
                      <small>This file already exists. It will be replaced only after the new SEG-Y has been written and verified successfully.</small>
                    )}
                    {!fileLoadError && !availableFiles.some((file) => file.fileName.toLowerCase() === visibleOutputName.toLowerCase()) && (
                      <small>SPI writes this filename inside storage/data/. Existing output files may be replaced after successful verification.</small>
                    )}
                  </>
                ) : (
                  <input
                    id={`parameter-${parameter.name}`}
                    value={currentValue}
                    onChange={(event) => setParameter(parameter.name, event.target.value)}
                    placeholder={parameter.type === "data_asset" ? "Select a SEG-Y file" : "Enter a value"}
                  />
                )}
                {!isDataAsset && !isOutputFilename && <small>{parameter.description}</small>}
              </div>
            );
          })}
          {error && <div className="form-error">{error}</div>}
        </div>
        <div className="pseudocode-panel">
          <div className="code-panel-heading"><Terminal size={14} />CONFIGURATION PREVIEW</div>
          <pre><code>{pseudocode}</code></pre>
          <div className="code-panel-note">Preview only · Paths are constrained to the fixed SPI storage directory.</div>
        </div>
      </div>
      <div className="modal-actions">
        <button className="secondary-button" onClick={onClose}>Cancel</button>
        <button className="primary-button" onClick={save}><Check size={15} />Save parameters</button>
      </div>
    </Modal>
  );
}

function RunHistoryPanel({
  projectId,
  runs,
  onClose,
  onClear,
}: {
  projectId: string;
  runs: RunRecord[];
  onClose: () => void;
  onClear: () => Promise<void>;
}) {
  const [currentRuns, setCurrentRuns] = useState<RunRecord[]>(runs);
  const [selectedRun, setSelectedRun] = useState<RunRecord | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [logs, setLogs] = useState<{ stdout: string; stderr: string; submissionError?: string; slurmJobId?: string } | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);
  const [logsError, setLogsError] = useState("");
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    setCurrentRuns(runs);
  }, [runs]);

  const activeRunKey = currentRuns
    .filter((run) => run.status === "Queued" || run.status === "Running")
    .map((run) => `${run.id}:${run.status}`)
    .join("|");

  useEffect(() => {
    if (!activeRunKey) return;
    let cancelled = false;

    const refreshStatuses = async () => {
      const activeRuns = currentRuns.filter((run) => run.status === "Queued" || run.status === "Running");
      const results = await Promise.allSettled(
        activeRuns.map((run) => apiRequest<RunRecord>(
          `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(run.id)}`,
        )),
      );
      if (cancelled) return;

      const updates = new Map<string, RunRecord>();
      results.forEach((result) => {
        if (result.status === "fulfilled") updates.set(result.value.id, result.value);
      });
      if (updates.size) {
        setCurrentRuns((existing) => existing.map((run) => updates.get(run.id) ?? run));
        setSelectedRun((selected) => selected ? updates.get(selected.id) ?? selected : selected);
      }
    };

    void refreshStatuses();
    const timer = window.setInterval(() => void refreshStatuses(), 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
    // activeRunKey captures only the active IDs/statuses; polling should restart when that set changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, activeRunKey]);

  const openRun = async (run: RunRecord) => {
    setSelectedRun(run);
    setLogs(null);
    setLogsError("");
    setLogsLoading(true);
    try {
      const latest = await apiRequest<RunRecord>(
        `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(run.id)}`,
      );
      setSelectedRun(latest);
      setCurrentRuns((existing) => existing.map((item) => item.id === latest.id ? latest : item));
      const result = await apiRequest<{ stdout: string; stderr: string; submissionError?: string; slurmJobId?: string }>(
        `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(run.id)}/logs`,
      );
      setLogs(result);
    } catch (error) {
      setLogsError(error instanceof Error ? error.message : "Could not retrieve HPC logs.");
    } finally {
      setLogsLoading(false);
    }
  };

  const refreshLogs = async () => {
    if (!selectedRun) return;
    await openRun(selectedRun);
  };

  const cancelRun = async () => {
    if (!selectedRun) return;
    setActionError("");
    try {
      const updated = await apiRequest<RunRecord>(
        `/api/projects/${encodeURIComponent(projectId)}/runs/${encodeURIComponent(selectedRun.id)}/cancel`,
        { method: "POST" },
      );
      setSelectedRun(updated);
      setCurrentRuns((existing) => existing.map((run) => run.id === updated.id ? updated : run));
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not cancel the SLURM job.");
    }
  };

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside className="history-drawer">
        <div className="drawer-header">
          <div><div className="eyebrow">PROJECT EXECUTIONS</div><h2>Run history</h2></div>
          <button className="icon-button" onClick={onClose} aria-label="Close run history"><X size={18} /></button>
        </div>
        {selectedRun ? (
          <div className="run-detail">
            <button className="back-link" onClick={() => { setSelectedRun(null); setLogs(null); setLogsError(""); setActionError(""); }}><ArrowLeft size={14} />All runs</button>
            <h3>{selectedRun.name}</h3>
            <span className={`run-status status-${selectedRun.status.toLowerCase()}`}>{selectedRun.status}</span>
            {selectedRun.slurmJobId && <p className="page-description">SLURM Job ID: {selectedRun.slurmJobId}</p>}
            {selectedRun.status === "Queued" || selectedRun.status === "Running" ? (
              <div className="modal-actions" style={{ justifyContent: "flex-start", marginTop: 12, paddingTop: 0, borderTop: 0 }}>
                <button className="danger-button compact-button" onClick={() => void cancelRun()}>Cancel job</button>
                <button className="secondary-button compact-button" onClick={() => void refreshLogs()}>Refresh logs</button>
              </div>
            ) : (
              <div className="modal-actions" style={{ justifyContent: "flex-start", marginTop: 12, paddingTop: 0, borderTop: 0 }}>
                <button className="secondary-button compact-button" onClick={() => void refreshLogs()}>Refresh logs</button>
              </div>
            )}
            {actionError && <div className="form-error">{actionError}</div>}
            {logsLoading ? (
              <div className="placeholder-panel compact-empty"><h2>Retrieving HPC logs</h2><p>Reading this run's stdout and stderr files.</p></div>
            ) : logsError ? (
              <div className="log-section"><strong>Could not retrieve logs</strong><pre>{logsError}</pre></div>
            ) : (
              <>
                {logs?.submissionError && <div className="log-section"><strong>Submission error</strong><pre>{logs.submissionError}</pre></div>}
                <div className="log-section"><strong>stdout · HPC log</strong><pre>{logs?.stdout || "No stdout output recorded yet."}</pre></div>
                <div className="log-section"><strong>stderr · HPC log</strong><pre>{logs?.stderr || "No stderr output recorded yet."}</pre></div>
              </>
            )}
          </div>
        ) : currentRuns.length === 0 ? (
          <div className="history-empty"><div className="empty-icon"><History size={24} /></div><h3>No runs yet</h3><p>Run All submissions and their SLURM job status will appear here.</p></div>
        ) : (
          <div className="run-list">
            {currentRuns.map((run) => (
              <button className="run-row" key={run.id} onDoubleClick={() => void openRun(run)} title="Double-click to retrieve stdout and stderr from the HPC">
                <div className="run-row-main"><strong>{run.name}</strong><span>{run.startedAt}{run.slurmJobId ? ` · Job ${run.slurmJobId}` : ""}</span></div>
                <span className={`run-status status-${run.status.toLowerCase()}`}>{run.status}</span>
                <ChevronRight size={15} />
              </button>
            ))}
          </div>
        )}
        {currentRuns.length > 0 && !selectedRun && (
          <div className="drawer-footer">
            {confirmClear ? <><span>Clear run records and HPC log files?</span><button className="text-button danger-text" onClick={() => { void onClear().then(() => setConfirmClear(false)); }}>Confirm clear</button><button className="text-button" onClick={() => setConfirmClear(false)}>Keep</button></> : <button className="text-button danger-text" onClick={() => setConfirmClear(true)}><Trash2 size={14} />Clear history</button>}
          </div>
        )}
      </aside>
    </div>
  );
}

function ClusterConfigurationsPage({ configs, onChange }: { configs: ClusterConfig[]; onChange: (configs: ClusterConfig[]) => void }) {
  const emptyConfig: Omit<ClusterConfig, "id"> = {
    name: "", jobName: "spi_job", partition: "", nodes: 1, tasks: 1, cpus: 1, time: "01:00:00", memory: "4G",
  };
  const [editing, setEditing] = useState<ClusterConfig | null>(null);
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [draft, setDraft] = useState<Omit<ClusterConfig, "id">>(emptyConfig);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const startNew = () => { setEditing(null); setDraft({ ...emptyConfig }); setConfigModalOpen(true); };
  const startEdit = (config: ClusterConfig) => { setEditing(config); setDraft({ ...config }); setConfigModalOpen(true); };
  const save = () => {
    if (!draft.name.trim() || !draft.partition.trim()) return;
    if (editing) onChange(configs.map((config) => config.id === editing.id ? { ...draft, id: editing.id } : config));
    else onChange([...configs, { ...draft, id: makeId() }]);
    setEditing(null);
    setConfigModalOpen(false);
  };
  const setField = <K extends keyof typeof draft,>(key: K, value: (typeof draft)[K]) => setDraft((current) => ({ ...current, [key]: value }));

  return (
    <>
      <div className="page-heading">
        <div><div className="eyebrow">COMPUTE RESOURCES</div><h1>Cluster Configurations</h1><p className="page-description">Manage reusable SLURM resource profiles for project executions.</p></div>
        <button className="primary-button" onClick={startNew}><Plus size={16} />New configuration</button>
      </div>
      {configs.length === 0 ? <div className="empty-projects compact-empty"><div className="empty-icon"><Server size={25} /></div><h2>No cluster configurations</h2><p>Create a configuration to define the SLURM resources used by your jobs.</p><button className="primary-button" onClick={startNew}><Plus size={15} />Create configuration</button></div> : (
        <div className="config-grid">{configs.map((config) => <div className="config-card" key={config.id}><div className="config-card-top"><div className="config-icon"><Server size={19} /></div><button className="text-button" onClick={() => startEdit(config)}>Edit</button></div><h2>{config.name}</h2><div className="partition-label">{config.partition}</div><div className="config-specs"><span><Cpu size={14} />{config.cpus} CPUs</span><span><HardDrive size={14} />{config.memory} RAM</span><span><Clock3 size={14} />{config.time}</span></div><div className="config-card-footer"><span>{config.nodes} node · {config.tasks} task</span><button className="icon-button danger-hover" aria-label={`Delete ${config.name}`} onClick={() => setConfirmDeleteId(config.id)}><Trash2 size={15} /></button></div></div>)}</div>
      )}

      {configModalOpen && (
        <Modal title={editing ? "Edit configuration" : "New cluster configuration"} eyebrow="SLURM PROFILE" onClose={() => { setEditing(null); setDraft({ ...emptyConfig }); setConfigModalOpen(false); }} wide>
          <div className="config-form-grid">
            <ConfigInput label="Configuration name" value={draft.name} onChange={(value) => setField("name", value)} placeholder="e.g. A100 Large" />
            <ConfigInput label="SLURM job name (-J)" value={draft.jobName} onChange={(value) => setField("jobName", value)} />
            <ConfigInput label="Partition (-p)" value={draft.partition} onChange={(value) => setField("partition", value)} placeholder="e.g. a100serv" />
            <ConfigInput label="Time limit (-t)" value={draft.time} onChange={(value) => setField("time", value)} placeholder="12:00:00" />
            <ConfigNumber label="Nodes (-N)" value={draft.nodes} onChange={(value) => setField("nodes", value)} />
            <ConfigNumber label="Tasks (-n)" value={draft.tasks} onChange={(value) => setField("tasks", value)} />
            <ConfigNumber label="CPUs per task (-c)" value={draft.cpus} onChange={(value) => setField("cpus", value)} />
            <ConfigInput label="Memory (--mem)" value={draft.memory} onChange={(value) => setField("memory", value)} placeholder="300G" />
          </div>
          <div className="slurm-preview"><div className="code-panel-heading"><Terminal size={14} />GENERATED SBATCH DIRECTIVES</div><pre>{`#SBATCH -J ${draft.jobName}\n#SBATCH -p ${draft.partition}\n#SBATCH -N ${draft.nodes}\n#SBATCH -n ${draft.tasks}\n#SBATCH -c ${draft.cpus}\n#SBATCH -t ${draft.time}\n#SBATCH --mem=${draft.memory}`}</pre><div className="code-panel-note">SPI assigns --output and --error automatically for every run: projects/&lt;project_id&gt;/&lt;run_id&gt;/stdout-%j.log and stderr-%j.log. These paths cannot be changed in cluster configurations.</div></div>
          <div className="modal-actions"><button className="secondary-button" onClick={() => { setEditing(null); setDraft({ ...emptyConfig }); setConfigModalOpen(false); }}>Cancel</button><button className="primary-button" disabled={!draft.name.trim() || !draft.partition.trim()} onClick={save}><Check size={15} />Save configuration</button></div>
        </Modal>
      )}

      {confirmDeleteId && <Modal title="Delete configuration?" eyebrow="CONFIRM ACTION" onClose={() => setConfirmDeleteId(null)}><p className="modal-intro">This removes the configuration from the local interface. Projects currently referring to it will need another profile selected.</p><div className="modal-actions"><button className="secondary-button" onClick={() => setConfirmDeleteId(null)}>Cancel</button><button className="danger-button" onClick={() => { onChange(configs.filter((config) => config.id !== confirmDeleteId)); setConfirmDeleteId(null); }}>Delete configuration</button></div></Modal>}
    </>
  );
}

function ConfigInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string }) {
  return <div className="form-field"><label>{label}</label><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} /></div>;
}

function ConfigNumber({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return <div className="form-field"><label>{label}</label><input type="number" min={1} value={value} onChange={(event) => onChange(Math.max(1, Number(event.target.value)))} /></div>;
}

function DataAssetsPage() {
  const [files, setFiles] = useState<HPCDataFile[]>([]);
  const [directory, setDirectory] = useState("storage/data/");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadFiles = async () => {
    setLoading(true);
    setError("");
    try {
      const result = await apiRequest<{ directory: string; files: HPCDataFile[] }>("/api/data-assets/files");
      setDirectory(result.directory);
      setFiles(result.files);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Could not load HPC data files.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadFiles(); }, []);

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
  };

  return (
    <>
      <div className="page-heading">
        <div><div className="eyebrow">INPUT DATA</div><h1>Data Assets</h1><p className="page-description">SEG-Y files currently available in the fixed HPC storage directory.</p></div>
        <button className="secondary-button" onClick={() => void loadFiles()} disabled={loading}><Database size={15} />Refresh files</button>
      </div>
      <div className="data-assets-location"><HardDrive size={15} /><span>Fixed HPC directory</span><code>{directory}</code></div>
      {loading ? (
        <div className="placeholder-panel"><div className="empty-icon"><Database size={25} /></div><h2>Reading HPC storage</h2><p>Listing SEG-Y files in the fixed data directory.</p></div>
      ) : error ? (
        <div className="placeholder-panel"><div className="empty-icon"><Database size={25} /></div><h2>Could not read HPC storage</h2><p>{error}</p><button className="secondary-button" onClick={() => void loadFiles()}>Try again</button></div>
      ) : files.length === 0 ? (
        <div className="placeholder-panel"><div className="empty-icon"><Database size={25} /></div><h2>No SEG-Y files found</h2><p>Place .sgy or .segy files directly in the fixed storage/data directory on the HPC, then refresh.</p></div>
      ) : (
        <div className="data-assets-list">
          <div className="data-assets-list-heading"><span>FILE</span><span>SIZE</span></div>
          {files.map((file) => (
            <div className="data-asset-row" key={file.relativePath}>
              <div className="data-asset-name"><div className="config-icon"><FileInput size={17} /></div><div><strong>{file.fileName}</strong><small>{file.relativePath}</small></div></div>
              <span className="data-asset-size">{formatSize(file.sizeBytes)}</span>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function SettingsPage() {
  return <><div className="page-heading"><div><div className="eyebrow">APPLICATION</div><h1>Settings</h1><p className="page-description">Application and connection preferences.</p></div></div><div className="placeholder-panel"><div className="empty-icon"><Settings size={25} /></div><h2>Settings</h2><p>Local backend and HPC connection settings will be managed here.</p></div></>;
}

function Modal({ title, eyebrow, onClose, children, wide = false }: { title: string; eyebrow: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className={`modal-card ${wide ? "modal-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={18} /></button></div>
        {children}
      </section>
    </div>
  );
}

export default App;
