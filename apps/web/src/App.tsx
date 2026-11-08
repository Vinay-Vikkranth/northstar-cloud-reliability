import { useEffect, useState } from "react";
import { ageLabel, api, can, login, restoreSession, type Session } from "./api";

type Meta = {
  banner: string;
  source_mode: string;
  investigator_mode: string;
  provider: string;
  model: string | null;
};

type Incident = {
  id: string;
  title: string;
  state: string;
  severity: string;
  account: string;
  owner: string;
  age_seconds: number;
  description?: string;
  problem?: string;
  diagnosis?: {
    cause: string;
    next_action: string;
    certainty: string;
    certainty_note: string;
    limitations: string;
    observations: string[];
    competing_explanations: { cause: string; reason: string }[];
  } | null;
  proposal?: {
    id: string;
    account: string;
    field: string;
    before: string;
    after: string;
    proposal_hash: string;
    expires_at: string;
    source_preconditions: Record<string, string | number>;
    destination_preconditions: Record<string, string | number>;
    approvals: { decision: string; reason: string }[];
  } | null;
  repair_state?: string | null;
  verification?: { outcome: string; limits: string; mismatch: boolean } | null;
  investigation?: { state: string; mode: string; provider: string; model: string | null } | null;
};

const NAV = [
  ["queue", "Incidents"],
  ["activity", "Activity"],
  ["operations", "Operations"],
  ["connectors", "Connectors"],
  ["evaluations", "Evaluations"],
  ["demo", "Demo controls"],
  ["guide", "Beginner guide"],
] as const;

function route(): { name: string; id?: string } {
  const hash = window.location.hash.replace(/^#\/?/, "");
  const [name, id] = hash.split("/");
  if (name === "incidents" && id) return { name: "incident", id };
  if (NAV.some(([key]) => key === name)) return { name };
  return { name: "queue" };
}

function go(path: string) {
  window.location.hash = path;
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState(route());

  useEffect(() => {
    restoreSession().then((value) => {
      setSession(value);
      setReady(true);
    });
    const onHash = () => setView(route());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (!ready) return <main className="main"><p>Loading session…</p></main>;
  if (!session) return <Login onSession={setSession} />;

  const workspace = session.workspaces.find((item) => item.id === session.workspaceId) || session.workspaces[0];
  return (
    <div className="shell">
      <nav className="nav" aria-label="Primary">
        <p className="brand">Northstar</p>
        {NAV.map(([key, label]) => (
          <button key={key} aria-current={view.name === key ? "page" : undefined} onClick={() => go(key)}>
            {label}
          </button>
        ))}
        <button
          onClick={async () => {
            await api("/api/v1/auth/logout", session, { method: "POST" });
            sessionStorage.removeItem("cir_workspace");
            setSession(null);
          }}
        >
          Sign out
        </button>
      </nav>
      <main className="main">
        <WorkspaceBar session={session} onSession={setSession} />
        {error && <p className="error" role="alert">{error}</p>}
        {view.name === "queue" && <Queue session={session} onError={setError} />}
        {view.name === "incident" && view.id && <IncidentView session={session} id={view.id} roles={workspace.roles} onError={setError} />}
        {view.name === "activity" && <Activity session={session} onError={setError} />}
        {view.name === "operations" && <Operations session={session} onError={setError} />}
        {view.name === "connectors" && <Connectors session={session} roles={workspace.roles} onError={setError} />}
        {view.name === "evaluations" && <Evaluations session={session} onError={setError} />}
        {view.name === "demo" && <Demo session={session} roles={workspace.roles} onError={setError} />}
        {view.name === "guide" && <Guide />}
      </main>
    </div>
  );
}

type OpsJob = {id:string;kind:string;state:string;attempts:number;max_attempts:number;duration_ms:number|null;traceparent:string|null;lease_expires:string|null};
type OpsData = {counts:Record<string,number>;repair_states:Record<string,number>;completion_rate:number|null;p50_ms:number|null;p95_ms:number|null;terminal_jobs:number;duration_samples:number;expired_leases:number;limits:string;slo:{status:string;success_target:number;p95_target_ms:number};alerts:{code:string;severity:string;message:string}[];jobs:OpsJob[]};
function Operations({session,onError}:{session:Session;onError:(error:string)=>void}){
  const [data,setData]=useState<OpsData|null>(null);
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{let alive=true;const load=()=>api<OpsData>("/api/v1/operations",session).then(value=>{if(alive)setData(value)}).catch(err=>{if(alive)onError(err instanceof Error?err.message:"Operations unavailable")});load();const timer=setInterval(load,5000);return()=>{alive=false;clearInterval(timer)}},[session.workspaceId,refresh]);
  if(!data)return <p>Loading operational observations…</p>;
  const metric=(label:string,value:string,detail:string)=><article className="card"><p className="muted">{label}</p><h2>{value}</h2><p>{detail}</p></article>;
  return <section><div className="row"><h1>Operations</h1><span className="pill">{data.slo.status.replaceAll("_"," ")}</span><button className="secondary" onClick={()=>setRefresh(refresh+1)}>Refresh</button></div>
    <p className="muted">Durable queue, lease recovery and release observations for this workspace.</p>
    <div className="grid">{metric("Terminal job success",data.completion_rate===null?"No samples":`${(data.completion_rate*100).toFixed(1)}%`,`${data.terminal_jobs} terminal jobs · objective 99%`)}{metric("Execution p95",data.p95_ms===null?"No samples":`${data.p95_ms.toFixed(1)} ms`,`p50 ${data.p50_ms?.toFixed(1)??"—"} ms · ${data.duration_samples} observations`)}{metric("Queue backlog",String((data.counts.pending??0)+(data.counts.leased??0)),`${data.counts.pending??0} pending · ${data.counts.leased??0} leased`)}{metric("Expired leases",String(data.expired_leases),`${data.counts.dead??0} dead letter jobs`)}</div>
    <h2>Alerts</h2>{data.alerts.length===0?<p>No queue alerts in the current observation. This is not a production availability guarantee.</p>:data.alerts.map(alert=><p className={alert.severity==="critical"?"error":"banner"} key={alert.code}>{alert.message}</p>)}
    <h2>Recent jobs</h2><div className="ops-table"><table><thead><tr><th>Job / kind</th><th>State / attempts</th><th>Duration</th><th>Trace context</th></tr></thead><tbody>{data.jobs.map(job=><tr key={job.id}><td><code>{job.id.slice(0,8)}</code><br/>{job.kind}</td><td>{job.state}<br/>{job.attempts}/{job.max_attempts}</td><td>{job.duration_ms===null?"—":`${job.duration_ms.toFixed(1)} ms`}</td><td><code title={job.traceparent??""}>{job.traceparent?job.traceparent.slice(3,35):"No incoming trace"}</code></td></tr>)}</tbody></table></div>
    <p className="muted">{data.limits}</p><p className="muted">A dead letter replay is an administrator API action with a required reason. Original approval and repair dispatch budgets are preserved. See the recovery runbook.</p></section>
}

function Login({ onSession }: { onSession: (session: Session) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  return (
    <main className="login card">
      <h1>Northstar Recovery</h1>
      <p className="muted">Sign in to review one account’s plan disagreement and, if you are an approver, allow a single plan write.</p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setError("");
          try {
            const session = await login(username, password);
            sessionStorage.setItem("cir_workspace", session.workspaceId);
            onSession(session);
            go("queue");
          } catch (err) {
            setError(err instanceof Error ? err.message : "Sign-in failed");
          }
        }}
      >
        <label className="field">Username<input name="username" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
        <label className="field">Password<input name="password" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} required /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" type="submit">Sign in</button>
      </form>
    </main>
  );
}

function WorkspaceBar({ session, onSession }: { session: Session; onSession: (session: Session) => void }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  useEffect(() => {
    api<Meta>("/api/v1/meta", session).then(setMeta).catch(() => setMeta(null));
  }, [session]);
  return (
    <>
      <div className="row">
        <label className="field">
          Workspace
          <select
            aria-label="Workspace"
            value={session.workspaceId}
            onChange={(event) => {
              sessionStorage.setItem("cir_workspace", event.target.value);
              onSession({ ...session, workspaceId: event.target.value });
            }}
          >
            {session.workspaces.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <p className="muted">Signed in as {session.username}</p>
      </div>
      {meta && (
        <>
          <p className="banner">{meta.banner}. Repairs require evidence and a separate approver. AI suggestions can be wrong.</p>
          <div className="modes">
            <span className="pill">Source: {meta.source_mode}</span>
            <span className="pill">Investigator: {meta.investigator_mode}</span>
            <span className="pill">Provider: {meta.provider}{meta.model ? ` / ${meta.model}` : ""}</span>
          </div>
        </>
      )}
    </>
  );
}

function Queue({ session, onError }: { session: Session; onError: (value: string) => void }) {
  const [state, setState] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ items: Incident[]; counts: Record<string, number> } | null>(null);
  async function load(nextState = state, nextQ = q) {
    try {
      const params = new URLSearchParams();
      if (nextState) params.set("state", nextState);
      if (nextQ) params.set("q", nextQ);
      setData(await api(`/api/v1/incidents?${params}`, session));
      onError("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not load incidents");
    }
  }
  useEffect(() => {
    load();
  }, [session.workspaceId]);
  return (
    <section>
      <h1>Incident queue</h1>
      <p className="muted">Each row is one account whose plan may disagree between the CRM and support.</p>
      <form className="row" onSubmit={(event) => { event.preventDefault(); load(); }}>
        <label>State<select aria-label="Filter by state" value={state} onChange={(event) => setState(event.target.value)}><option value="">All</option>{["OPEN", "INVESTIGATING", "AWAITING_APPROVAL", "NEEDS_INFORMATION", "EXECUTING", "VERIFYING", "RESOLVED", "ESCALATED"].map((item) => <option key={item}>{item}</option>)}</select></label>
        <label>Search<input aria-label="Search incidents" value={q} onChange={(event) => setQ(event.target.value)} /></label>
        <button className="secondary" type="submit">Apply</button>
      </form>
      <div className="counts">{data && Object.entries(data.counts).map(([key, value]) => <span key={key}>{key}: {value}</span>)}</div>
      {!data ? <p>Loading incidents…</p> : data.items.length === 0 ? <p className="card">No incidents match this filter.</p> : (
        <table>
          <thead><tr><th>Account</th><th>Status</th><th>Severity</th><th>Owner</th><th>Age</th></tr></thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id} className="buttonish" tabIndex={0} onClick={() => go(`incidents/${item.id}`)} onKeyDown={(event) => { if (event.key === "Enter") go(`incidents/${item.id}`); }}>
                <td>{item.account}<div className="muted">{item.title}</div></td>
                <td className="state">{item.state}</td>
                <td>{item.severity}</td>
                <td>{item.owner}</td>
                <td>{ageLabel(item.age_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function IncidentView({ session, id, roles, onError }: { session: Session; id: string; roles: string[]; onError: (value: string) => void }) {
  const [item, setItem] = useState<Incident | null>(null);
  const [reason, setReason] = useState("Reviewed the CRM revision and the support account.");
  const [busy, setBusy] = useState("");
  async function load() {
    try {
      setItem(await api(`/api/v1/incidents/${id}`, session));
      onError("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Incident not available");
    }
  }
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [id, session.workspaceId]);
  async function act(path: string, body?: unknown) {
    setBusy(path);
    try {
      await api(path, session, { method: "POST", body: body ? JSON.stringify(body) : "{}" });
      await load();
    } catch (err) {
      onError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setBusy("");
    }
  }
  if (!item) return <p>Loading incident…</p>;
  const proposal = item.proposal;
  const accepted = item.repair_state === "SUCCEEDED" || item.repair_state === "RECONCILING" || item.repair_state === "VERIFIED";
  const verified = item.verification?.outcome === "verified";
  return (
    <article className="stack">
      <div>
        <button className="secondary" onClick={() => go("queue")}>Back to queue</button>
        <h1>{item.account}</h1>
        <p>{verified && proposal ? `The support plan was changed from ${proposal.before} to ${proposal.after} after approval. A fresh read confirmed the result. The evidence below preserves the original investigation.` : item.problem || item.description}</p>
        <p className="muted">{item.state} · {item.severity} · owner {item.owner} · age {ageLabel(item.age_seconds)}</p>
      </div>
      <section className="card">
        <h2>What the two systems showed</h2>
        {verified && <p className="muted">Before-repair snapshot. See Verification below for the confirmed result.</p>}
        {proposal ? (
          <div className="diff">
            <div><strong>CRM at investigation</strong><p>Plan {String(proposal.source_preconditions.plan)}</p><p>{proposal.source_preconditions.revision == null ? "This source uses its timestamp and record fingerprint for freshness checks." : `Revision ${proposal.source_preconditions.revision}`}</p></div>
            <div><strong>Support at investigation</strong><p>Plan {proposal.before}</p><p>Revision {String(proposal.destination_preconditions.revision)}</p></div>
          </div>
        ) : <p>Investigation has not captured both records yet.</p>}
      </section>
      <section className="card">
        <h2>{verified ? "Original diagnosis" : "Diagnosis"}</h2>
        {item.investigation && <p className="muted">Mode {item.investigation.mode}. Provider {item.investigation.provider}. Investigation checkpoint: {item.investigation.state}{verified ? " (historical; the repair is now verified)" : ""}.</p>}
        {item.diagnosis ? (
          <>
            <p>Cause: <strong>{item.diagnosis.cause}</strong>. Next step: <strong>{item.diagnosis.next_action}</strong>.</p>
            <p className="muted">{item.diagnosis.certainty}. {item.diagnosis.certainty_note}</p>
            <ul>{item.diagnosis.observations.map((line) => <li key={line}>{line}</li>)}</ul>
            {item.diagnosis.limitations.length > 0 && <p>Unknown or limited: {item.diagnosis.limitations}</p>}
          </>
        ) : <p>No diagnosis yet.</p>}
        <div className="row">
          {can(roles, "operator") && <button className="primary" disabled={Boolean(busy)} onClick={() => act(`/api/v1/incidents/${id}/investigate`)}>Investigate</button>}
          {can(roles, "operator") && <button className="secondary" disabled={Boolean(busy)} onClick={() => act(`/api/v1/incidents/${id}/cancel`)}>Cancel</button>}
          {can(roles, "operator") && <button className="secondary" disabled={Boolean(busy)} onClick={() => act(`/api/v1/incidents/${id}/reopen`)}>Reopen</button>}
        </div>
      </section>
      <section className="card">
        <h2>Approval</h2>
        {proposal ? (
          <>
            <p>Account {proposal.account}. Field {proposal.field}. {proposal.approvals.length > 0 ? "Original approval deadline" : "Expires"}: {new Date(proposal.expires_at).toLocaleString()}.</p>
            <div className="diff">
              <div><strong>Before</strong><p>{proposal.before}</p></div>
              <div><strong>{verified ? "Verified after repair" : "After, if approved"}</strong><p>{proposal.after}</p></div>
            </div>
            <p className="muted">Approval is tied to this exact account and change. Changing the proposal requires a new review.</p>
            {proposal.approvals.map((approval, index) => <p key={index}>{approval.decision}: {approval.reason}</p>)}
            {can(roles, "approver") && proposal.approvals.length === 0 && !item.repair_state && (
              <form onSubmit={(event) => { event.preventDefault(); act(`/api/v1/proposals/${proposal.id}/approve`, { proposal_hash: proposal.proposal_hash, reason }); }}>
                <label className="field">Reason<textarea aria-label="Approval reason" value={reason} onChange={(event) => setReason(event.target.value)} required /></label>
                <div className="row">
                  <button className="primary" type="submit" disabled={Boolean(busy)}>Approve repair</button>
                  <button className="danger" type="button" disabled={Boolean(busy)} onClick={() => act(`/api/v1/proposals/${proposal.id}/reject`, { proposal_hash: proposal.proposal_hash, reason })}>Reject</button>
                </div>
              </form>
            )}
            {!can(roles, "approver") && <p>Your role can inspect this diff. Approval stays with an approver.</p>}
          </>
        ) : <p>There is no repair proposal. A missing diagnosis, a healthy record, or an unsafe case does not invent one.</p>}
      </section>
      <section className="card">
        <h2>Verification</h2>
        <p>Repair accepted: {accepted ? "yes, the destination accepted the idempotent write" : "not yet"}.</p>
        <p>Repair verified: {verified ? "yes, a fresh read matched the approved plan" : item.verification ? `no (${item.verification.outcome})` : "not yet"}.</p>
        <p>Incident resolved: {item.state === "RESOLVED" ? "yes" : "no"}.</p>
        {item.verification && <p>{item.verification.limits}</p>}
        {item.repair_state && <p className="muted">Repair state {item.repair_state}.</p>}
      </section>
    </article>
  );
}

function Activity({ session, onError }: { session: Session; onError: (value: string) => void }) {
  const [items, setItems] = useState<{ action: string; resource_type: string; at: string }[]>([]);
  useEffect(() => {
    api<{ items: { action: string; resource_type: string; at: string }[] }>("/api/v1/activity", session).then((body) => setItems(body.items)).catch((err) => onError(err.message));
  }, [session.workspaceId]);
  return (
    <section>
      <h1>Activity</h1>
      {items.length === 0 ? <p className="card">No audit events in this workspace yet.</p> : (
        <table><thead><tr><th>When</th><th>Action</th><th>Resource</th></tr></thead><tbody>
          {items.map((item, index) => <tr key={`${item.at}-${index}`}><td>{new Date(item.at).toLocaleString()}</td><td>{item.action}</td><td>{item.resource_type}</td></tr>)}
        </tbody></table>
      )}
    </section>
  );
}

function Connectors({ session, roles, onError }: { session: Session; roles: string[]; onError: (value: string) => void }) {
  const [items, setItems] = useState<Record<string, string>[]>([]);
  const [name, setName] = useState("HubSpot contacts");
  async function load() {
    try { setItems(await api("/api/v1/connectors", session)); } catch (err) { onError(err instanceof Error ? err.message : "Could not load connectors"); }
  }
  useEffect(() => { load(); }, [session.workspaceId]);
  return (
    <section className="stack">
      <h1>Connectors</h1>
      <p className="muted">Secrets stay in the server environment. This page shows whether a secret is configured, not the secret.</p>
      <div className="grid">
        {items.map((item) => (
          <article className="card" key={item.id}>
            <h2>{item.name}</h2>
            <p>{item.provider} · {item.origin_host}</p>
            <p>Secret configured: {String(item.secret_configured)}. Health: {item.last_health_status || "not checked"}.</p>
            <p>{item.setup_note}</p>
            <button className="secondary" onClick={async () => { await api(`/api/v1/connectors/${item.id}/health`, session, { method: "POST" }); await load(); }}>Check health</button>
          </article>
        ))}
        {items.length === 0 && <p className="card">No connectors in this workspace.</p>}
      </div>
      {can(roles, "administrator") && (
        <form className="card" onSubmit={async (event) => {
          event.preventDefault();
          try {
            await api("/api/v1/connectors", session, { method: "POST", body: JSON.stringify({ name, provider: "hubspot", mapping_version: "v1" }) });
            await load();
          } catch (err) { onError(err instanceof Error ? err.message : "Could not add connector"); }
        }}>
          <h2>Add an allowlisted connector</h2>
          <label className="field">Name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
          <p className="muted">Only crm_simulator, support_service, and hubspot are accepted. A pasted URL is rejected.</p>
          <button className="primary" type="submit">Add HubSpot connector</button>
        </form>
      )}
    </section>
  );
}

function Evaluations({ session, onError }: { session: Session; onError: (value: string) => void }) {
  const [body, setBody] = useState<{ items: { id: string; approach: string; split: string; mode: string; model: string | null; summary: Record<string, number | string> }[]; live_status: string } | null>(null);
  useEffect(() => {
    api<typeof body>("/api/v1/evaluations", session).then(setBody).catch((err) => onError(err.message));
  }, [session.workspaceId]);
  return (
    <section className="stack">
      <h1>Evaluation</h1>
      <p>Live model status: {body?.live_status || "loading"}. Synthetic and live results are labeled separately. Raw model benchmarks do not measure the application's approval and repair safeguards. Local model monetary cost is unmeasured.</p>
      {(body?.items || []).map((item) => (
        <article className="card" key={item.id}>
          <h2>{item.approach} · {item.split}</h2>
          <p>Mode {item.mode}{item.model ? ` (${item.model})` : ""}. Sample {String(item.summary.sample_size)}. Cause correct {String(item.summary.diagnosis_correct)}. Unsafe suggestions {String(item.summary.unsafe_repair_proposals)}.</p>
          <p className="muted">{String(item.summary.note || "")}</p>
        </article>
      ))}
      {body && body.items.length === 0 && <p className="card">No report is stored yet. Run evaluations/run_baseline.py and seed the database.</p>}
    </section>
  );
}

const SCENARIOS = ["missing_update", "healthy", "mapping_mismatch", "credentials_expired", "destination_outage", "contradictory", "injection", "rate_limited"];

function Demo({ session, roles, onError }: { session: Session; roles: string[]; onError: (value: string) => void }) {
  const [message, setMessage] = useState("");
  if (!can(roles, "administrator")) return <p className="card">Demo controls are limited to an administrator of the synthetic northstar-demo workspace.</p>;
  return (
    <section className="stack">
      <h1>Synthetic demo controls</h1>
      <p className="banner">These controls reset shared local simulator data, including accounts used by practice workspaces. Existing incident history can then refer to deleted accounts. Use the Beginner guide for practice instead. HubSpot is not changed.</p>
      <div className="row">
        {SCENARIOS.map((scenario) => (
          <button key={scenario} className="secondary" onClick={async () => {
            if (!window.confirm("This replaces shared simulator records, including practice accounts. Continue?")) return;
            try {
              const result = await api<Record<string, unknown>>("/api/v1/demo/scenarios", session, { method: "POST", body: JSON.stringify({ scenario }) });
              setMessage(`${scenario}: opened ${String(result.incidents_opened ?? "n/a")} incident check(s).`);
              onError("");
            } catch (err) { onError(err instanceof Error ? err.message : "Scenario failed"); }
          }}>{scenario}</button>
        ))}
        <button className="danger" onClick={async () => {
          if (!window.confirm("This resets shared simulator records, including practice accounts. Continue?")) return;
          try {
            await api("/api/v1/demo/reset", session, { method: "POST" });
            setMessage("Reset the synthetic workspace to the missing-update scenario.");
          } catch (err) { onError(err instanceof Error ? err.message : "Reset failed"); }
        }}>Reset demo workspace</button>
      </div>
      {message && <p className="ok">{message}</p>}
    </section>
  );
}

function Guide() {
  return (
    <article className="card stack">
      <h1>Beginner guide</h1>
      <p>A customer paid for Premium, but the support system still treats them as Basic. Northstar finds the disagreement, proposes a specific correction, asks a separate person to approve it, and checks whether the correction actually worked.</p>
      <h2>The pipeline in plain English</h2>
      <p>Read both systems → compare the evidence → propose a change → human approval → update support → read again to verify.</p>
      <ul>
        <li><strong>CRM:</strong> the source of the customer's correct subscription plan. This can be a local fictional service or a configured HubSpot test contact.</li>
        <li><strong>Investigator:</strong> gathers records and runbook evidence. Rules mode uses fixed logic; live mode can ask a configured AI model for a diagnosis. An AI answer alone cannot authorize a repair.</li>
        <li><strong>Approver:</strong> reviews the exact before/after values. Changes to the underlying records can invalidate an old proposal.</li>
        <li><strong>Repair worker:</strong> applies the approved change to the local support service with safeguards against duplicate writes.</li>
        <li><strong>Verifier:</strong> reads the destination again. Accepted means the write was received; verified means the fresh record matches; resolved is the resulting incident status.</li>
      </ul>
      <h2>Your first walkthrough</h2>
      <ol>
        <li>Sign in as northstar.operator using your local credentials, select Vinay Practice, and open Alex Practice. If that workspace is not available, prepare it with the learning workspace script before continuing.</li>
        <li>Choose Investigate. The worker reads the CRM and the support service over HTTP.</li>
        <li>Check the evidence: CRM says Premium, support says Basic. Review the proposed Basic → Premium correction. No repair has happened yet.</li>
        <li>Sign out, sign in as northstar.approver, select the same workspace and incident, add a reason, then approve or reject.</li>
        <li>Wait until verification says a fresh read matched. Resolved means that check passed.</li>
      </ol>
      <p>The original Before evidence stays visible after repair so you can explain what changed. Repeating this walkthrough needs a fresh practice account; do not reset the shared demo to repeat it.</p>
      <h2>Test the cases where it should not write</h2>
      <ul>
        <li><strong>Jordan Healthy:</strong> both plans already match. Investigation should produce no repair proposal.</li>
        <li><strong>Casey Unknown Plan:</strong> the source says Gold, which has no approved mapping. The system should ask for information rather than invent a plan.</li>
      </ul>
      <h2>What is real in this run?</h2>
      <p>Check the workspace banner and investigation mode. Connecting an AI model and connecting HubSpot are two separate steps. A configured connection is not proof of a successful live request. The support destination is a local demonstration service, not a production customer account.</p>
      <h2>Explain it in an interview</h2>
      <p>“I built a customer integration recovery system that investigates inconsistent subscription records, produces an evidence-backed repair proposal, requires separate human approval, and verifies the result. I tested healthy records and unsupported mappings as well as successful repairs.”</p>
      <p>Add live-model or HubSpot results only after those checks pass. This demo demonstrates integration and reliability engineering; it does not yet establish customer adoption or measured business savings.</p>
    </article>
  );
}
