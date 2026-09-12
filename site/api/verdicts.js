// One JSON document per triage page (?doc=verdicts for tiles, ?doc=scenes for scenes)
// in Vercel Blob holds every verdict, comment and note. GET returns it; PUT (or a sendBeacon POST) replaces it. Both need the
// shared key, so a leaked URL can't overwrite a curation pass.
import { put, get } from "@vercel/blob";

const DOCS = { verdicts: "triage/verdicts.json", scenes: "triage/scenes.json", labels: "triage/labels.json" };
const EMPTY = { v: {}, c: {}, n: "", t: 0 };

export default async function handler(req, res) {
  const key = req.headers["x-triage-key"] || req.query.key;
  if (!process.env.TRIAGE_KEY || key !== process.env.TRIAGE_KEY) {
    return res.status(401).json({ error: "bad key" });
  }
  res.setHeader("Cache-Control", "no-store");
  const PATH = DOCS[req.query.doc] || DOCS.verdicts;

  if (req.method === "GET") {
    const found = await get(PATH, { access: "private", useCache: false });
    if (!found) return res.status(200).json(EMPTY);
    res.setHeader("Content-Type", "application/json");
    return res.status(200).send(await new Response(found.stream).text());
  }

  if (req.method === "PUT" || req.method === "POST") {
    const body = typeof req.body === "string" ? req.body : JSON.stringify(req.body);
    if (!body || body.length > 2_000_000) return res.status(400).json({ error: "bad body" });
    await put(PATH, body, { access: "private", contentType: "application/json", addRandomSuffix: false, allowOverwrite: true });
    return res.status(200).json({ ok: true, size: body.length, at: new Date().toISOString() });
  }

  res.setHeader("Allow", "GET, PUT, POST");
  return res.status(405).end();
}
