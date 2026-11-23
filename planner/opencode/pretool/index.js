import {createChannel, RELEASE} from "../bridge.mjs";

const TOOLS = new Set(["read", "write", "edit"]);

export async function install(ctx, admit) {
  if (ctx.app.version !== RELEASE) throw new Error("Incompatible OpenCode release");
  let session = null;
  const registered = [];
  const hook = async (domain, name, callback) => {
    await ctx[domain].hook(name, callback);
    registered.push(domain + "." + name);
  };
  const check = async id => {
    if (!session || id !== session) throw new Error("Unassigned hook session");
    const {data: plugins} = await ctx.plugin.list();
    if (!Array.isArray(plugins) || plugins.some(p => p.source?.type !== "builtin" && p.id !== "planner.pretool"))
      throw new Error("Unreviewed plugin in hook session");
  };
  await ctx.tool.transform(editor => {
    for (const tool of editor.list()) if (!TOOLS.has(tool.id)) editor.remove(tool.id);
  });
  await ctx.mcp.transform(editor => {for (const [name] of editor.list()) editor.remove(name);});
  await ctx.reference.transform(editor => {for (const [name] of editor.list()) editor.remove(name);});
  await hook("session", "prompt", async event => {
    if (event.prompt.files?.length || event.prompt.skills?.length || event.prompt.agents?.length)
      throw new Error("Hook mode requires plain text prompts");
    if (!session) {
      await admit(event.sessionID, "session.bind", {});
      session = event.sessionID;
    }
    await check(event.sessionID);
    await admit(session, "prompt", {});
  });
  await hook("tool", "execute.before", async event => {
    await check(event.sessionID);
    if (!TOOLS.has(event.tool)) throw new Error("Tool unavailable in hook mode");
    await admit(session, "tool.before", {tool: event.tool, call_id: event.id, input: event.input});
  });
  await hook("tool", "execute.after", async event => {
    await check(event.sessionID);
    await admit(session, "tool.after", {tool: event.tool, call_id: event.id});
  });
  await hook("permission", "evaluate", async event => {
    try {
      await check(event.sessionID);
      await admit(session, "permission.evaluate", {action: event.action,
        resources: event.resources, source: event.source ?? null});
      event.effect = "allow";
    } catch {
      event.effect = "deny";
      event.message = "Supervisor denied operation";
    }
  });
  await hook("shell", "create.before", () => {throw new Error("Shell is disabled in hook mode");});
  await admit(null, "hello", {release: RELEASE, registered});
}

export default {id: "planner.pretool", async setup(ctx) {
  const admit = createChannel({endpoint: process.env.PLANNER_BROKER_URL,
    token: process.env.PLANNER_BROKER_TOKEN,
    timeoutMs: Number(process.env.PLANNER_BROKER_TIMEOUT_MS || 10000),
    maxBytes: Number(process.env.PLANNER_MAX_BODY_BYTES || 4194304)});
  for (const name of Object.keys(process.env)) if (name.startsWith("PLANNER_")) delete process.env[name];
  await install(ctx, admit);
}};
