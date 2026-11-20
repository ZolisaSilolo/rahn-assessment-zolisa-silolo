// AWS Signature Version 4 for browser fetch() calls, using only the Web Crypto API.
// Used to sign requests to the Lambda Function URL (AuthType AWS_IAM) with the temporary
// credentials the Cognito identity pool issues to a signed-in staff member.
// tests/test_frontend_sigv4.py checks this output against botocore's SigV4 implementation.

const encoder = new TextEncoder();

const toHex = (buffer) =>
  [...new Uint8Array(buffer)].map((b) => b.toString(16).padStart(2, "0")).join("");

async function sha256Hex(data) {
  return toHex(await crypto.subtle.digest("SHA-256", encoder.encode(data)));
}

async function hmac(key, data) {
  const rawKey = typeof key === "string" ? encoder.encode(key) : key;
  const cryptoKey = await crypto.subtle.importKey("raw", rawKey, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return crypto.subtle.sign("HMAC", cryptoKey, encoder.encode(data));
}

// RFC 3986 encoding as SigV4 requires (encodeURIComponent leaves !'()* unescaped).
const uriEncode = (value) =>
  encodeURIComponent(value).replace(/[!'()*]/g, (c) => "%" + c.charCodeAt(0).toString(16).toUpperCase());

export function amzDate(date) {
  return date.toISOString().replace(/[:-]|\.\d{3}/g, ""); // e.g. 20261120T144500Z
}

/**
 * Returns the headers to send (authorization, x-amz-date, x-amz-content-sha256 and, for temporary
 * credentials, x-amz-security-token). The browser sets Host itself; it is still signed.
 */
export async function signRequest({ method, url, body = "", region, service, credentials, date = new Date() }) {
  const target = new URL(url);
  const timestamp = amzDate(date);
  const day = timestamp.slice(0, 8);
  const payloadHash = await sha256Hex(body);

  const headers = { host: target.host, "x-amz-content-sha256": payloadHash, "x-amz-date": timestamp };
  if (credentials.sessionToken) headers["x-amz-security-token"] = credentials.sessionToken;

  const names = Object.keys(headers).sort();
  const canonicalHeaders = names.map((name) => `${name}:${String(headers[name]).trim()}\n`).join("");
  const signedHeaders = names.join(";");
  const canonicalUri =
    target.pathname.split("/").map((segment) => uriEncode(decodeURIComponent(segment))).join("/") || "/";
  const canonicalQuery = [...target.searchParams.entries()]
    .map(([k, v]) => [uriEncode(k), uriEncode(v)])
    .sort((a, b) => (a[0] === b[0] ? (a[1] < b[1] ? -1 : 1) : a[0] < b[0] ? -1 : 1))
    .map(([k, v]) => `${k}=${v}`)
    .join("&");

  const canonicalRequest = [method.toUpperCase(), canonicalUri, canonicalQuery, canonicalHeaders, signedHeaders, payloadHash].join("\n");
  const scope = `${day}/${region}/${service}/aws4_request`;
  const stringToSign = ["AWS4-HMAC-SHA256", timestamp, scope, await sha256Hex(canonicalRequest)].join("\n");

  let signingKey = await hmac("AWS4" + credentials.secretAccessKey, day);
  signingKey = await hmac(signingKey, region);
  signingKey = await hmac(signingKey, service);
  signingKey = await hmac(signingKey, "aws4_request");
  const signature = toHex(await hmac(signingKey, stringToSign));

  const result = {
    ...headers,
    authorization: `AWS4-HMAC-SHA256 Credential=${credentials.accessKeyId}/${scope}, SignedHeaders=${signedHeaders}, Signature=${signature}`,
  };
  delete result.host;
  return result;
}
