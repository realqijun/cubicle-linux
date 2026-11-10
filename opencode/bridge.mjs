// OpenCode 2.2.5 Promise plugin. This module never approves permissions itself.
export const RELEASE = "2.2.5";
const TOOLS = new Set(["read", "write", "edit", "glob", "grep", "shell"]);

export function createChannel({endpoint, token, timeoutMs = 10000, maxBytes = 4194304}, fetcher = fetch) {
  const url = new URL(endpoint);
  if (url.protocol !== "http:" || !["127.0.0.1", "[::1]"].includes(url.hostname) ||
      url.pathname !== "/admit" || url.search || url.hash || url.username || url.password ||
      typeof token !== "string" || !/^[A-Za-z0-9_-]{32,}$/.test(token) ||
      !Number.isFinite(timeoutMs) || timeoutMs <= 0 || !Number.isSafeInteger(maxBytes) || maxBytes < 1)
    throw new Error("Invalid supervisor transport configuration");
  let sequence = 0, tail = Promise.resolve(), closed = false;
  return (session_id, route, payload) => {
    const turn = tail.then(async () => {
      if (closed) throw new Error("Supervisor channel is closed");
      const body = JSON.stringify({protocol: 1, sequence: ++sequence, route, session_id, payload});
      if (new TextEncoder().encode(body).length > maxBytes) throw new Error("Supervisor payload limit exceeded");
      let response, result;
      try {
        response = await fetcher(url, {method: "POST", headers: {
          "content-type": "application/json", authorization: "Bearer " + token,
        }, body, signal: AbortSignal.timeout(timeoutMs), redirect: "error"});
        if (!response.ok) throw new Error();
        result = JSON.parse(await boundedBody(response, maxBytes));
        if (typeof result?.allowed !== "boolean") throw new Error();
      } catch {
        closed = true;
        throw new Error("Supervisor unavailable or invalid response");
      }
      if (!result.allowed) throw new Error("Supervisor denied operation");
      return result;
    });
    tail = turn.catch(() => {});
    return turn;
  };
}

async function boundedBody(message, maxBytes) {
  const reader = message.body?.getReader();
  if (!reader) return "";
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      size += value.length;
      if (size > maxBytes) throw new Error("Model body limit exceeded");
      chunks.push(value);
    }
  } catch (error) {
    await reader.cancel();
    throw error;
  } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {bytes.set(chunk, offset); offset += chunk.length;}
  return new TextDecoder("utf-8", {fatal: true}).decode(bytes);
}

export async function install(ctx, admit, {maxBodyBytes = 4194304, credentialNames = []} = {}) {
  if (ctx.app.version !== RELEASE) throw new Error("Incompatible OpenCode release");
  let session = null;
  const contexts = new Map(), requests = new WeakMap(), registered = [];
  const register = async (domain, name, callback) => {
    await ctx[domain].hook(name, callback); registered.push(domain + "." + name);
  };
  const sources = new Map(), pending = {primary: [], compaction: []};
  const requireSession = id => {
    if (!session || id !== session) throw new Error("Unbound worker session");
  };
  const checkPlugins = async () => {
    const {data: plugins} = await ctx.plugin.list();
    if (!Array.isArray(plugins)) throw new Error("Unsupported plugin inventory shape");
    if (plugins.some(plugin => plugin.source.type !== "builtin" && plugin.id !== "planner.supervisor"))
      throw new Error("Unreviewed plugin is active");
  };
  await ctx.tool.transform(editor => {
    for (const tool of editor.list()) if (!TOOLS.has(tool.id)) editor.remove(tool.id);
  });
  await ctx.mcp.transform(editor => {for (const [name] of editor.list()) editor.remove(name);});
  await ctx.reference.transform(editor => {for (const [name] of editor.list()) editor.remove(name);});
  await register("session", "prompt", async event => {
    await checkPlugins();
    if (event.prompt.files?.length || event.prompt.skills?.length || event.prompt.agents?.length)
      throw new Error("Unverified prompt attachments or native delegation are disabled");
    if (!session) {
      await admit(event.sessionID, "session.bind", {});
      session = event.sessionID;
    }
    requireSession(event.sessionID);
    const result = await admit(session, "prompt", {message_id: event.messageID, prompt: event.prompt});
    if (result.receipt) sources.set(event.messageID, {kind: "prompt", receipt: result.receipt});
  });
  await register("tool", "execute.before", async event => {
    await checkPlugins();
    requireSession(event.sessionID);
    if (!TOOLS.has(event.tool)) throw new Error("Unsupported worker tool");
    await admit(session, "tool.before", {tool: event.tool, call_id: event.id, input: event.input});
  });
  await register("tool", "execute.after", async event => {
    requireSession(event.sessionID);
    await admit(session, "tool.after", {tool: event.tool, call_id: event.id, status: event.status,
      ...(event.status === "completed" ? {result: event.result} : {error: {message: event.error?.message}})});
  });
  for (const [hook, kind] of [["context", "primary"], ["compaction", "compaction"],
                            ["generate", "generate"], ["title", "title"]]) {
    await register("session", hook, async event => {
      requireSession(event.sessionID);
      const transcript = await ctx.session.context({sessionID: session});
      const ids = new Set(event.messages.map(message => message.id).filter(Boolean));
      const native = transcript.find(message => message.type === "compaction" && message.native?.messages);
      if (native) throw new Error("Native compaction windows require verified source mapping");
      for (const [type, outputKind] of [["assistant", "primary"], ["compaction", "compaction"]]) {
        const unknown = transcript.filter(message => message.type === type && ids.has(message.id) && !sources.has(message.id));
        if (unknown.length && unknown.length !== pending[outputKind].length) throw new Error("Untracked generated context");
        for (const message of unknown) sources.set(message.id, {kind: outputKind, receipt: pending[outputKind].shift()});
      }
      const message_sources = event.messages.filter(message => sources.has(message.id))
        .map(message => ({message_id: message.id, ...sources.get(message.id)}));
      const result = await admit(session, hook, {kind, system: event.system, messages: event.messages,
        options: event.options, message_sources, ...(event.tools ? {tools: event.tools} : {})});
      if (typeof result.receipt !== "string") throw new Error("Missing model context receipt");
      contexts.set(kind, result.receipt);
    });
  }
  await register("session", "model.request", async event => {
    requireSession(event.sessionID);
    await admit(session, "model.request", {kind: event.kind, model: event.model,
      url_origin: new URL(event.baseURL).origin, context_receipt: contexts.get(event.kind)});
  });
  await register("session", "http.request", async event => {
    await checkPlugins();
    requireSession(event.sessionID);
    const url = new URL(event.request.url);
    if (url.username || url.password || url.search || event.request.method !== "POST")
      throw new Error("Unsupported native provider request");
    const result = await admit(session, "provider.http.request", {kind: event.kind,
      model: event.model, method: event.request.method, url_origin: url.origin,
      context_receipt: contexts.get(event.kind), body: await boundedBody(event.request.clone(), maxBodyBytes)});
    if (typeof result.receipt !== "string") throw new Error("Missing provider request receipt");
    requests.set(event.request, result.receipt);
  });
  await register("session", "http.response", async event => {
    requireSession(event.sessionID);
    const request_receipt = requests.get(event.request);
    if (!request_receipt) throw new Error("Untracked provider response");
    const result = await admit(session, "provider.http.response", {kind: event.kind, request_receipt,
      status: event.response.status, body: await boundedBody(event.response.clone(), maxBodyBytes)});
    if (event.response.ok && pending[event.kind] && result.receipt) pending[event.kind].push(result.receipt);
  });
  for (const hook of ["experimental.ws.handshake", "experimental.ws.send", "experimental.ws.receive"]) {
    await register("session", hook, () => {throw new Error("Unverified WebSocket transport is disabled");});
  }
  for (const hook of ["sdk", "language"]) {
    await register("aisdk", hook, () => {throw new Error("Unverified legacy SDK transport is disabled");});
  }
  await register("permission", "evaluate", async event => {
    requireSession(event.sessionID);
    // Native permission prompts cannot become a second approval channel.
    try {
      await admit(session, "permission.evaluate", {action: event.action,
        resources: event.resources, source: event.source ?? null});
      event.effect = "allow";
    } catch {
      event.effect = "deny";
      event.message = "Supervisor denied operation";
    }
  });
  await register("shell", "create.before", async event => {
    requireSession(session);
    for (const key of Object.keys(event.env)) if (key.startsWith("PLANNER_")) delete event.env[key];
    for (const key of [...credentialNames, "OPENCODE_PASSWORD", "OPENCODE_SERVER_PASSWORD"]) delete event.env[key];
    await admit(session, "shell.before", {command: event.command, cwd: event.cwd,
      shell: event.shell, timeout: event.timeout});
  });
  await admit(null, "hello", {release: RELEASE, registered});
}

export default {id: "planner.supervisor", async setup(ctx) {
  const endpoint = process.env.PLANNER_BROKER_URL;
  const token = process.env.PLANNER_BROKER_TOKEN;
  const timeoutMs = Number(process.env.PLANNER_BROKER_TIMEOUT_MS || 10000);
  const maxBytes = Number(process.env.PLANNER_MAX_BODY_BYTES || 4194304);
  const credentialNames = JSON.parse(process.env.PLANNER_CREDENTIAL_VARS || "[]");
  if (!Array.isArray(credentialNames) || credentialNames.some(name => typeof name !== "string"))
    throw new Error("Invalid credential environment configuration");
  for (const key of Object.keys(process.env)) if (key.startsWith("PLANNER_")) delete process.env[key];
  await install(ctx, createChannel({endpoint, token, timeoutMs, maxBytes}), {maxBodyBytes: maxBytes, credentialNames});
}};
