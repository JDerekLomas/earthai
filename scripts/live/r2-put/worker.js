// A one-route upload gateway for the earthai-clouds R2 bucket, so a box with no Cloudflare credentials can
// publish the live loop's files. PUT /<key> with `Authorization: Bearer <UPLOAD_TOKEN>` writes the body to
// the bucket under <key>; HEAD /<key> says whether it exists (size in Content-Length). Nothing else: no
// GET (the bucket's own public domain serves reads), no DELETE, no listing. The token is a worker secret
// (`wrangler secret put UPLOAD_TOKEN`), held on the box in its root-only secrets file.
//
// Why not an R2 API token on the box: the laptop's wrangler OAuth token has no scope to mint account API
// tokens (only the dashboard can); this worker is deployable with the scopes it has (workers:write).
// One dashboard click (R2 -> Manage API tokens) would replace it with S3 credentials and `aws s3 cp`.
const TYPES = { mp4: 'video/mp4', webp: 'image/webp', json: 'application/json', h264: 'application/octet-stream', jpg: 'image/jpeg' };
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = decodeURIComponent(url.pathname.replace(/^\/+/, ''));
    if (!key || key.includes('..')) return new Response('bad key', { status: 400 });
    const auth = request.headers.get('Authorization') || '';
    if (!env.UPLOAD_TOKEN || auth !== `Bearer ${env.UPLOAD_TOKEN}`) return new Response('unauthorized', { status: 401 });
    if (request.method === 'HEAD') {
      const o = await env.BUCKET.head(key);
      return o ? new Response(null, { status: 200, headers: { 'Content-Length': String(o.size), 'ETag': o.httpEtag } }) : new Response(null, { status: 404 });
    }
    if (request.method !== 'PUT') return new Response('method', { status: 405 });
    const ext = key.split('.').pop();
    const contentType = request.headers.get('Content-Type') || TYPES[ext] || 'application/octet-stream';
    const len = request.headers.get('Content-Length');
    // a stream of known length goes straight through; without a length the body is buffered (small files only)
    const body = len ? request.body : await request.arrayBuffer();
    const o = await env.BUCKET.put(key, body, { httpMetadata: { contentType } });
    return new Response(JSON.stringify({ key: o.key, size: o.size, etag: o.httpEtag }), { headers: { 'Content-Type': 'application/json' } });
  }
};
