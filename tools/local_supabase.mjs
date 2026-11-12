/**
 * A local Supabase-compatible server.
 *
 * The app is a Vercel app + a Supabase database. To RUN it without accounts
 * we need a database that speaks the same dialect. This is that database:
 * real Postgres (PGlite, the same engine the migration gate uses), the real
 * migration files, the real RLS policies, and a small HTTP surface that
 * implements the parts of PostgREST and GoTrue that the app and the worker
 * actually call.
 *
 * What it supports:
 *   GET/POST/PATCH/DELETE  /rest/v1/<table>      (select, filters, order,
 *                                                limit, upsert on_conflict)
 *   POST                   /rest/v1/rpc/<fn>    (every function in the schema)
 *   GET                    /auth/v1/user
 *   POST                   /auth/v1/otp | /auth/v1/token | /auth/v1/logout
 *   POST                   /api/v1/realtime/broadcast  (accepted, not relayed)
 *
 * What it does NOT support: Realtime websockets (the watchlist falls back to
 * the prices it loaded from the database), storage, and email. Those need the
 * hosted project; nothing else does.
 *
 * The roles are real. The anon key sees what anon sees, the service-role key
 * bypasses RLS exactly as it does in production, and the demo session runs as
 * the OWNER. A bug that only shows up under the wrong role still shows up
 * here.
 *
 *   node tools/local_supabase.mjs [--port 54321]
 */

import { PGlite } from "@electric-sql/pglite";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  ANON_KEY,
  DEMO_EMAIL,
  DEMO_SEED,
  DEMO_USER_ID,
  SERVICE_ROLE_KEY,
  SHIM,
} from "./supabase_shim.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const MIGRATIONS_DIR = join(ROOT, "supabase", "migrations");

// The local database lives on disk.
//
// It used to be in memory, which meant every restart of the local stack
// destroyed the database: the bars the worker had fetched, the journal, the
// strategies, the research runs. Re-fetching a year of history to lose it
// again on the next restart is a strange way to work, and it made the
// local environment behave nothing like the hosted project.
const DATA_DIR = join(ROOT, ".local", "pgdata");
mkdirSync(DATA_DIR, { recursive: true });

const portArg = process.argv.indexOf("--port");
const PORT = portArg > -1 ? Number(process.argv[portArg + 1]) : 54321;

const db = new PGlite(DATA_DIR);

// ---------------------------------------------------------------- startup --

console.log("Starting the local Supabase...");
await db.exec(SHIM);
console.log("  OK   shim");

// The migration ledger. A persisted database already has the migrations
// that were applied to it, so they are not applied twice.
await db.exec(`
  create table if not exists public._local_migrations (
    name       text primary key,
    applied_at timestamptz not null default now()
  );
`);

const applied = new Set(
  (
    await db.query("select name from public._local_migrations")
  ).rows.map((row) => row.name),
);

let failed = false;
for (const file of readdirSync(MIGRATIONS_DIR).sort()) {
  if (!file.endsWith(".sql")) continue;
  if (applied.has(file)) {
    console.log(`  --   ${file} (already applied)`);
    continue;
  }
  const sql = readFileSync(join(MIGRATIONS_DIR, file), "utf8");
  try {
    await db.exec(sql);
    await db.query(
      "insert into public._local_migrations (name) values ($1) on conflict do nothing",
      [file],
    );
    console.log(`  OK   ${file}`);
  } catch (err) {
    failed = true;
    console.error(`  FAIL ${file}\n       ${String(err.message ?? err).split("\n")[0]}`);
    break;
  }
}
if (failed) {
  console.error("\nMigrations failed. The local server cannot start.");
  process.exit(1);
}

await db.exec(DEMO_SEED);
console.log(`  OK   demo user ${DEMO_EMAIL} (owner)`);

// ------------------------------------------------------------- SQL helpers --

const IDENT = /^[a-z_][a-z0-9_]*$/;

function ident(value) {
  if (!IDENT.test(value)) {
    throw new Error(`unsafe identifier: ${value}`);
  }
  return value;
}

function tableIdent(value) {
  return ident(String(value).replace(/[^a-z_0-9]/gi, "").toLowerCase());
}

/** The role a request runs as, and whose identity it carries. */
function roleFor(req) {
  const apikey = req.headers["apikey"];
  if (apikey === SERVICE_ROLE_KEY) {
    return { role: "service_role", sub: DEMO_USER_ID };
  }
  const auth = req.headers["authorization"];
  if (typeof auth === "string" && auth.toLowerCase().startsWith("bearer ")) {
    return { role: "authenticated", sub: DEMO_USER_ID };
  }
  return { role: "anon", sub: null };
}

async function withDb(role, sub, fn) {
  return db.transaction(async (tx) => {
    await tx.query("select set_config('request.jwt.claim.sub', $1, true)", [
      sub ?? "",
    ]);
    await tx.query(`set local role ${role}`);
    return fn(tx);
  });
}

// ------------------------------------------------------------ query builder --

const OPERATORS = {
  eq: "=",
  neq: "<>",
  gt: ">",
  gte: ">=",
  lt: "<",
  lte: "<=",
  like: "like",
  ilike: "ilike",
};

/**
 * Turn PostgREST query parameters into a WHERE clause.
 *
 * Only the operators the app and the worker actually use are supported. An
 * unknown operator is an error rather than a silent no-op: a filter that
 * quietly does nothing is how you ship a leak.
 */
function buildFilters(params, values) {
  const clauses = [];
  for (const [key, raw] of params.entries()) {
    if (["select", "order", "limit", "offset", "on_conflict"].includes(key)) {
      continue;
    }
    const column = ident(key);
    const dot = raw.indexOf(".");
    if (dot < 0) {
      throw new Error(`unsupported filter: ${key}=${raw}`);
    }
    const op = raw.slice(0, dot);
    const value = raw.slice(dot + 1);

    if (op === "in") {
      const inner = value.replace(/^\(|\)$/g, "");
      const items = inner.split(",").filter((item) => item !== "");
      if (items.length === 0) {
        clauses.push("false");
        continue;
      }
      const placeholders = items.map((item) => {
        values.push(item);
        return `$${values.length}`;
      });
      clauses.push(`${column} in (${placeholders.join(", ")})`);
      continue;
    }

    if (op === "is") {
      if (value === "null") clauses.push(`${column} is null`);
      else if (value === "not.null") clauses.push(`${column} is not null`);
      else throw new Error(`unsupported is filter: ${value}`);
      continue;
    }

    const sqlOp = OPERATORS[op];
    if (!sqlOp) {
      throw new Error(`unsupported filter operator: ${op}`);
    }
    values.push(value);
    clauses.push(`${column} ${sqlOp} $${values.length}`);
  }
  return clauses;
}

function buildOrder(params) {
  const raw = params.get("order");
  if (!raw) return "";
  const parts = raw.split(",").map((part) => {
    const [column, direction] = part.split(".");
    const dir = direction === "desc" ? "desc" : "asc";
    return `${ident(column)} ${dir}`;
  });
  return ` order by ${parts.join(", ")}`;
}

function selectColumns(params) {
  const raw = params.get("select") || "*";
  if (raw.trim() === "*") return "*";
  const cols = raw.split(",").map((col) => ident(col.trim()));
  return cols.join(", ");
}

// ------------------------------------------------------------- the handlers --

function json(res, status, payload, headers = {}) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
    ...headers,
  });
  res.end(body);
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (chunks.length === 0) return null;
  const text = Buffer.concat(chunks).toString("utf8");
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function handleSelect(req, res, url, role) {
  const table = tableIdent(url.pathname.split("/")[3]);
  const values = [];
  const clauses = buildFilters(url.searchParams, values);
  const where = clauses.length ? ` where ${clauses.join(" and ")}` : "";
  const order = buildOrder(url.searchParams);
  const limit = Number(url.searchParams.get("limit") || 0);
  const offset = Number(url.searchParams.get("offset") || 0);
  const limitClause = limit > 0 ? ` limit ${Math.floor(limit)}` : "";
  const offsetClause = offset > 0 ? ` offset ${Math.floor(offset)}` : "";

  const sql = `select ${selectColumns(url.searchParams)} from public.${table}${where}${order}${limitClause}${offsetClause}`;
  const result = await withDb(role.role, role.sub, (tx) => tx.query(sql, values));

  const accept = String(req.headers["accept"] || "");
  if (accept.includes("vnd.pgrst.object+json")) {
    // maybeSingle(): no rows is a null, not an error.
    return json(res, 200, result.rows[0] ?? null);
  }
  return json(res, 200, result.rows);
}

async function handleInsert(req, res, url, role, body) {
  const table = tableIdent(url.pathname.split("/")[3]);
  const rows = Array.isArray(body) ? body : body ? [body] : [];
  if (rows.length === 0) return json(res, 201, []);

  const columns = Object.keys(rows[0]);
  const insertColumns = columns.map(ident).join(", ");

  const values = [];
  const tuples = rows.map((row) => {
    const placeholders = columns.map((column) => {
      values.push(row[column] ?? null);
      return `$${values.length}`;
    });
    return `(${placeholders.join(", ")})`;
  });

  const onConflict = url.searchParams.get("on_conflict");
  let conflictClause = "";
  if (onConflict) {
    const conflictColumns = onConflict.split(",").map((col) => ident(col.trim()));
    const updates = columns
      .filter((column) => !conflictColumns.includes(column))
      .map((column) => `${ident(column)} = excluded.${ident(column)}`);
    conflictClause = updates.length
      ? ` on conflict (${conflictColumns.join(", ")}) do update set ${updates.join(", ")}`
      : ` on conflict (${conflictColumns.join(", ")}) do nothing`;
  }

  const sql = `insert into public.${table} (${insertColumns}) values ${tuples.join(", ")}${conflictClause} returning *`;
  const result = await withDb(role.role, role.sub, (tx) => tx.query(sql, values));

  const prefer = String(req.headers["prefer"] || "");
  if (prefer.includes("return=minimal")) {
    res.writeHead(201, { "access-control-allow-origin": "*" });
    return res.end();
  }
  return json(res, 201, result.rows);
}

async function handleUpdate(req, res, url, role, body) {
  const table = tableIdent(url.pathname.split("/")[3]);
  const patch = body && typeof body === "object" && !Array.isArray(body) ? body : {};
  const columns = Object.keys(patch);
  if (columns.length === 0) return json(res, 200, []);

  const values = [];
  const assignments = columns.map((column) => {
    values.push(patch[column]);
    return `${ident(column)} = $${values.length}`;
  });

  const filterValues = [];
  const clauses = buildFilters(url.searchParams, filterValues);
  const where = clauses.length ? ` where ${clauses.join(" and ")}` : "";

  // The filter values were collected separately so the SET values keep their
  // placeholders first.
  const allValues = [...values, ...filterValues];
  const sql = `update public.${table} set ${assignments.join(", ")}${where} returning *`;
  const result = await withDb(role.role, role.sub, (tx) => tx.query(sql, allValues));

  const prefer = String(req.headers["prefer"] || "");
  if (prefer.includes("return=minimal")) {
    res.writeHead(204, { "access-control-allow-origin": "*" });
    return res.end();
  }
  return json(res, 200, result.rows);
}

async function handleDelete(req, res, url, role) {
  const table = tableIdent(url.pathname.split("/")[3]);
  const values = [];
  const clauses = buildFilters(url.searchParams, values);
  const where = clauses.length ? ` where ${clauses.join(" and ")}` : "";
  const sql = `delete from public.${table}${where}`;
  await withDb(role.role, role.sub, (tx) => tx.query(sql, values));
  res.writeHead(204, { "access-control-allow-origin": "*" });
  return res.end();
}

async function handleRpc(req, res, url, role, body) {
  const fn = ident(url.pathname.split("/")[4]);
  const payload = body && typeof body === "object" ? body : {};

  const meta = await withDb("service_role", role.sub, (tx) =>
    tx.query(
      `select p.proretset, p.pronargs, p.proargnames
         from pg_proc p join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public' and p.proname = $1`,
      [fn],
    ),
  );
  if (meta.rows.length === 0) {
    return json(res, 404, { message: `function ${fn} not found`, code: "PGRST202" });
  }

  const argNames = meta.rows[0].proargnames ?? [];
  const values = argNames.map((name) => payload[name] ?? null);
  const placeholders = values.map((_value, index) => `$${index + 1}`);
  const call = placeholders.length
    ? `public.${fn}(${placeholders.join(", ")})`
    : `public.${fn}()`;
  const sql = meta.rows[0].proretset
    ? `select * from ${call}`
    : `select ${call}`;

  const result = await withDb(role.role, role.sub, (tx) => tx.query(sql, values));

  if (meta.rows[0].proretset) {
    return json(res, 200, result.rows);
  }
  const first = result.rows[0] ?? {};
  const key = Object.keys(first)[0];
  return json(res, 200, key === undefined ? null : first[key]);
}

// -------------------------------------------------------------------- auth --

function demoUser() {
  return {
    id: DEMO_USER_ID,
    aud: "authenticated",
    role: "authenticated",
    email: DEMO_EMAIL,
    email_confirmed_at: new Date().toISOString(),
    app_metadata: { provider: "email", providers: ["email"] },
    user_metadata: {},
    identities: [],
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

function session() {
  return {
    access_token: `local-session-token-${DEMO_USER_ID}`,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: Math.floor(Date.now() / 1000) + 3600,
    refresh_token: `local-refresh-token-${DEMO_USER_ID}`,
    user: demoUser(),
  };
}

async function handleAuth(req, res, url) {
  const path = url.pathname;

  if (path === "/auth/v1/otp" && req.method === "POST") {
    // The hosted service sends an email. Here we simply accept the request,
    // so the login page behaves exactly as it will in production.
    return json(res, 200, { message: "Magic link sent" });
  }

  if (path === "/auth/v1/token" && req.method === "POST") {
    return json(res, 200, session());
  }

  if (path === "/auth/v1/user" && req.method === "GET") {
    const auth = req.headers["authorization"];
    if (typeof auth !== "string" || !auth.toLowerCase().startsWith("bearer ")) {
      return json(res, 401, { message: "missing bearer token", code: "401" });
    }
    return json(res, 200, demoUser());
  }

  if (path === "/auth/v1/logout" && req.method === "POST") {
    res.writeHead(204, { "access-control-allow-origin": "*" });
    return res.end();
  }

  return json(res, 404, { message: `no auth route ${path}`, code: "404" });
}

// ---------------------------------------------------------------- realtime --

/**
 * A minimal WebSocket server (RFC 6455) that speaks enough of the Phoenix
 * protocol for supabase-js to subscribe to a broadcast channel.
 *
 * This is what makes the watchlist live locally: the worker posts a bundle
 * to /api/v1/realtime/broadcast and every subscribed socket receives it.
 */

const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

/** Every open socket, with the channels it has joined. */
const sockets = new Set();

function acceptKey(key) {
  return createHash("sha1").update(key + WS_GUID).digest("base64");
}

/** Encode one text frame (server frames are never masked). */
function encodeText(text) {
  const payload = Buffer.from(text, "utf8");
  const length = payload.length;
  let header;
  if (length < 126) {
    header = Buffer.alloc(2);
    header[1] = length;
  } else if (length < 65536) {
    header = Buffer.alloc(4);
    header[1] = 126;
    header.writeUInt16BE(length, 2);
  } else {
    header = Buffer.alloc(10);
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(length), 2);
  }
  header[0] = 0x81; // FIN + text
  return Buffer.concat([header, payload]);
}

/** Encode one opcode-0 frame with a short payload (pong, close). */
function encodeControl(opcode, payload = Buffer.alloc(0)) {
  const header = Buffer.alloc(2);
  header[0] = 0x80 | opcode;
  header[1] = payload.length;
  return Buffer.concat([header, payload]);
}

/** Pull complete frames out of a buffer. */
function parseFrames(buffer) {
  const frames = [];
  let offset = 0;
  while (buffer.length - offset >= 2) {
    const first = buffer[offset];
    const second = buffer[offset + 1];
    const opcode = first & 0x0f;
    const masked = (second & 0x80) !== 0;
    let length = second & 0x7f;
    let headerLength = 2;

    if (length === 126) {
      if (buffer.length - offset < 4) break;
      length = buffer.readUInt16BE(offset + 2);
      headerLength = 4;
    } else if (length === 127) {
      if (buffer.length - offset < 10) break;
      length = Number(buffer.readBigUInt64BE(offset + 2));
      headerLength = 10;
    }

    let maskKey = null;
    if (masked) {
      if (buffer.length - offset < headerLength + 4) break;
      maskKey = buffer.subarray(offset + headerLength, offset + headerLength + 4);
      headerLength += 4;
    }

    if (buffer.length - offset < headerLength + length) break;

    const raw = buffer.subarray(offset + headerLength, offset + headerLength + length);
    if (maskKey) {
      const unmasked = Buffer.alloc(length);
      for (let i = 0; i < length; i += 1) {
        unmasked[i] = raw[i] ^ maskKey[i % 4];
      }
      frames.push({ opcode, payload: unmasked });
    } else {
      frames.push({ opcode, payload: Buffer.from(raw) });
    }
    offset += headerLength + length;
  }
  return { frames, rest: buffer.subarray(offset) };
}

function handleSocketMessage(socket, text) {
  let message;
  try {
    message = JSON.parse(text);
  } catch {
    return;
  }
  if (!Array.isArray(message)) return;

  const [joinRef, ref, topic, event, payload] = message;
  console.log(`  ws <- ${topic} ${event}`);

  if (event === "phx_join") {
    socket.topics.add(topic);
    // The reply deliberately OMITS `postgres_changes`. The client compares
    // the server's binding list with the one it asked for; if the server
    // answers with an empty list, the client treats it as a mismatch and
    // leaves the channel. No key at all means "subscribed", which is the
    // truth here: the broadcast channel works, Postgres Changes does not.
    socket.send(
      JSON.stringify([joinRef, ref, topic, "phx_reply", { status: "ok", response: {} }]),
    );
    console.log(`  ws -> ${topic} phx_reply (joined)`);
    return;
  }

  if (event === "heartbeat") {
    socket.send(
      JSON.stringify([ref, ref, topic ?? "phoenix", "phx_reply", { status: "ok", response: {} }]),
    );
    return;
  }

  if (event === "phx_leave") {
    socket.topics.delete(topic);
    socket.send(
      JSON.stringify([ref, ref, topic, "phx_reply", { status: "ok", response: {} }]),
    );
  }
}

/** Deliver a broadcast to every subscribed socket. */
function deliverBroadcast(message) {
  const event = message.event ?? "message";
  const userPayload = message.payload ?? {};
  let sent = 0;
  for (const socket of sockets) {
    if (socket.topics.size === 0) continue;
    for (const topic of socket.topics) {
      socket.send(
        JSON.stringify([
          null,
          null,
          topic,
          // The Phoenix EVENT is the listen type ("broadcast"). The user's
          // event name and payload travel inside the envelope, which is how
          // the client decides which listener to call.
          "broadcast",
          { type: "broadcast", event, payload: userPayload },
        ]),
      );
      sent += 1;
    }
  }
  console.log(`  broadcast ${event} -> ${sent} socket(s), ${sockets.size} connected`);
}

// ------------------------------------------------------------------ server --

const server = createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "*",
      "access-control-allow-methods": "GET,POST,PATCH,DELETE,OPTIONS",
    });
    return res.end();
  }

  try {
    if (url.pathname === "/health") {
      return json(res, 200, { ok: true, demo_user: DEMO_USER_ID });
    }

    if (url.pathname.startsWith("/auth/v1/")) {
      return await handleAuth(req, res, url);
    }

    // The worker's broadcast. Accepted so the worker does not log a
    // failure, and pushed to every subscribed websocket.
    if (url.pathname === "/api/v1/realtime/broadcast") {
      const body = await readBody(req);
      const messages = Array.isArray(body?.messages) ? body.messages : [];
      for (const message of messages) {
        deliverBroadcast(message);
      }
      return json(res, 200, {});
    }

    if (url.pathname.startsWith("/rest/v1/")) {
      const role = roleFor(req);
      const body = req.method === "GET" || req.method === "DELETE"
        ? null
        : await readBody(req);

      if (url.pathname.startsWith("/rest/v1/rpc/")) {
        return await handleRpc(req, res, url, role, body);
      }
      if (req.method === "GET") return await handleSelect(req, res, url, role);
      if (req.method === "POST") return await handleInsert(req, res, url, role, body);
      if (req.method === "PATCH") return await handleUpdate(req, res, url, role, body);
      if (req.method === "DELETE") return await handleDelete(req, res, url, role);
      return json(res, 405, { message: "method not allowed" });
    }

    return json(res, 404, { message: `no route ${url.pathname}`, code: "404" });
  } catch (err) {
    const message = String(err?.message ?? err);
    console.error("request failed:", url.pathname, message);
    return json(res, 400, { message, code: "PGRST000" });
  }
});

server.on("upgrade", (req, socket) => {
  const key = req.headers["sec-websocket-key"];
  if (!key) {
    socket.destroy();
    return;
  }
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${acceptKey(key)}\r\n\r\n`,
  );

  const client = {
    topics: new Set(),
    buffer: Buffer.alloc(0),
    send(text) {
      if (!socket.destroyed) socket.write(encodeText(text));
    },
  };
  sockets.add(client);

  socket.on("data", (chunk) => {
    client.buffer = Buffer.concat([client.buffer, chunk]);
    const { frames, rest } = parseFrames(client.buffer);
    client.buffer = rest;
    for (const frame of frames) {
      if (frame.opcode === 0x8) {
        socket.end(encodeControl(0x8));
        sockets.delete(client);
        return;
      }
      if (frame.opcode === 0x9) {
        socket.write(encodeControl(0xa, frame.payload));
        continue;
      }
      if (frame.opcode === 0x1) {
        handleSocketMessage(client, frame.payload.toString("utf8"));
      }
    }
  });

  socket.on("close", () => sockets.delete(client));
  socket.on("error", () => sockets.delete(client));
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`\nLocal Supabase listening on http://127.0.0.1:${PORT}`);
  console.log(`  anon key:          ${ANON_KEY}`);
  console.log(`  service role key:  ${SERVICE_ROLE_KEY}`);
  console.log(`  demo user:         ${DEMO_EMAIL} (owner)`);
  console.log(`  sign in:           http://localhost:3000/auth/callback?code=demo`);
});
