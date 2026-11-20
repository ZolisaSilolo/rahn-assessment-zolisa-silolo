// FitDesk staff web app.
// Sign-in: Cognito managed login (authorization code + PKCE, state and nonce checked).
// API access: the ID token is exchanged for short-lived AWS credentials (Cognito identity pool,
// enhanced flow), which only allow invoking the FitDesk Function URL; each request is SigV4-signed.
// Safety: model and customer text is only ever written with textContent (never innerHTML), and
// a reply is recorded as "sent" only after the staff member clicks Send.

import config from "./config.js";
import { signRequest } from "./sigv4.js";

const $ = (id) => document.getElementById(id);
const store = window.sessionStorage; // cleared when the tab closes; no refresh token is kept
const REDIRECT_URI = window.location.origin + "/";
const LOGIN_PROVIDER = `cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`;
const MAX_CHARS = 1000;

let awsCredentials = null;
let currentEnquiry = null;

// ------------------------------------------------------------------ helpers
const base64Url = (bytes) =>
  btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

const randomString = () => base64Url(crypto.getRandomValues(new Uint8Array(32)));

function decodeJwtPayload(token) {
  const part = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(decodeURIComponent(escape(atob(part))));
}

function show(id, visible) {
  $(id).hidden = !visible;
}

function setStatus(text, kind = "info") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`;
  show("status", Boolean(text));
}

// ------------------------------------------------------------------ sign-in (code + PKCE)
async function startLogin() {
  const verifier = randomString();
  const challenge = base64Url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = randomString();
  const nonce = randomString();
  store.setItem("pkce_verifier", verifier);
  store.setItem("oauth_state", state);
  store.setItem("oauth_nonce", nonce);

  const params = new URLSearchParams({
    response_type: "code",
    client_id: config.clientId,
    redirect_uri: REDIRECT_URI,
    scope: "openid email",
    code_challenge: challenge,
    code_challenge_method: "S256",
    state,
    nonce,
  });
  window.location.assign(`https://${config.cognitoDomain}/oauth2/authorize?${params}`);
}

async function completeLogin(code, state) {
  const expectedState = store.getItem("oauth_state");
  const verifier = store.getItem("pkce_verifier");
  store.removeItem("oauth_state");
  store.removeItem("pkce_verifier");
  window.history.replaceState({}, "", REDIRECT_URI); // drop ?code= from the address bar
  if (!expectedState || state !== expectedState || !verifier) {
    throw new Error("Sign-in response did not match this browser session. Please sign in again.");
  }

  const response = await fetch(`https://${config.cognitoDomain}/oauth2/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: config.clientId,
      code,
      redirect_uri: REDIRECT_URI,
      code_verifier: verifier,
    }),
  });
  if (!response.ok) throw new Error("Sign-in failed while exchanging the code. Please try again.");
  const tokens = await response.json();

  const claims = decodeJwtPayload(tokens.id_token);
  const expectedNonce = store.getItem("oauth_nonce");
  store.removeItem("oauth_nonce");
  if (claims.nonce !== expectedNonce || claims.aud !== config.clientId || claims.exp * 1000 < Date.now()) {
    throw new Error("Sign-in token failed validation. Please sign in again.");
  }
  store.setItem("id_token", tokens.id_token);
}

function currentIdToken() {
  const token = store.getItem("id_token");
  if (!token) return null;
  try {
    if (decodeJwtPayload(token).exp * 1000 > Date.now() + 30_000) return token;
  } catch {
    /* fall through */
  }
  store.removeItem("id_token");
  return null;
}

function signOut() {
  store.clear();
  const params = new URLSearchParams({ client_id: config.clientId, logout_uri: REDIRECT_URI });
  window.location.assign(`https://${config.cognitoDomain}/logout?${params}`);
}

// ------------------------------------------------------------------ AWS credentials (identity pool)
async function cognitoIdentity(target, payload) {
  const response = await fetch(`https://cognito-identity.${config.region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "content-type": "application/x-amz-json-1.1",
      "x-amz-target": `AWSCognitoIdentityService.${target}`,
    },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(`Could not get AWS access (${data.__type || response.status}).`);
  return data;
}

async function getCredentials() {
  if (awsCredentials && awsCredentials.expiresAt - Date.now() > 60_000) return awsCredentials;
  const idToken = currentIdToken();
  if (!idToken) throw new Error("Your session has expired. Please sign in again.");
  const logins = { [LOGIN_PROVIDER]: idToken };
  const { IdentityId } = await cognitoIdentity("GetId", { IdentityPoolId: config.identityPoolId, Logins: logins });
  const { Credentials } = await cognitoIdentity("GetCredentialsForIdentity", { IdentityId, Logins: logins });
  awsCredentials = {
    accessKeyId: Credentials.AccessKeyId,
    secretAccessKey: Credentials.SecretKey,
    sessionToken: Credentials.SessionToken,
    expiresAt: Credentials.Expiration * 1000,
  };
  return awsCredentials;
}

// ------------------------------------------------------------------ API calls (SigV4)
async function api(method, path, payload) {
  const url = config.apiUrl.replace(/\/$/, "") + path;
  const body = payload === undefined ? "" : JSON.stringify(payload);
  const signed = await signRequest({
    method, url, body, region: config.region, service: "lambda", credentials: await getCredentials(),
  });
  const response = await fetch(url, {
    method,
    headers: { ...signed, ...(body ? { "content-type": "application/json" } : {}) },
    body: body || undefined,
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    /* empty body */
  }
  return { status: response.status, data };
}

// ------------------------------------------------------------------ UI
function resetDraft() {
  currentEnquiry = null;
  show("draft-panel", false);
  $("draft-text").textContent = "";
  $("send").disabled = false;
  $("decline").disabled = false;
}

async function onDraft(event) {
  event.preventDefault();
  resetDraft();
  const message = $("message").value.trim();
  if (!message || message.length > MAX_CHARS) {
    setStatus(`Enter a customer message of 1 to ${MAX_CHARS} characters.`, "error");
    return;
  }
  $("draft-btn").disabled = true;
  setStatus("Drafting a reply from the approved price list and availability…");
  try {
    const { status, data } = await api("POST", "/enquiries", { message });
    if (status !== 201) {
      setStatus(`${data.error || "Request failed."} Nothing was sent. (HTTP ${status})`, "error");
      return;
    }
    currentEnquiry = data.id;
    $("draft-text").textContent = data.draft_reply;
    show("escalation-note", Boolean(data.needs_escalation));
    show("draft-panel", true);
    setStatus("Review the draft. Nothing is recorded as sent until you click Send.");
  } catch (err) {
    setStatus(`${err.message} Nothing was sent.`, "error");
  } finally {
    $("draft-btn").disabled = false;
  }
}

async function onDecision(approve) {
  if (!currentEnquiry) return;
  $("send").disabled = true;
  $("decline").disabled = true;
  try {
    const { status, data } = await api("POST", `/enquiries/${encodeURIComponent(currentEnquiry)}/decision`, { approve });
    if (status !== 200) {
      setStatus(`${data.error || "Request failed."} (HTTP ${status})`, "error");
      return;
    }
    const label = { sent: "Approved and recorded as sent. Copy the reply into WhatsApp.",
                    rejected: "Declined. Not sent.", escalated_not_sent: "Declined. Not sent." }[data.status];
    setStatus(`${label || data.status} (enquiry ${currentEnquiry})`, data.status === "sent" ? "ok" : "info");
    currentEnquiry = null;
  } catch (err) {
    setStatus(`${err.message} The decision was not recorded.`, "error");
    $("send").disabled = false;
    $("decline").disabled = false;
  }
}

async function main() {
  $("sign-in").addEventListener("click", () => startLogin().catch((e) => setStatus(e.message, "error")));
  $("sign-out").addEventListener("click", signOut);
  $("enquiry-form").addEventListener("submit", onDraft);
  $("send").addEventListener("click", () => onDecision(true));
  $("decline").addEventListener("click", () => onDecision(false));
  $("message").addEventListener("input", () => {
    $("count").textContent = `${$("message").value.length} / ${MAX_CHARS}`;
  });

  const params = new URLSearchParams(window.location.search);
  try {
    if (params.has("error")) throw new Error(`Sign-in was not completed (${params.get("error")}).`);
    if (params.has("code")) await completeLogin(params.get("code"), params.get("state"));
  } catch (err) {
    setStatus(err.message, "error");
  }

  const idToken = currentIdToken();
  show("signed-out", !idToken);
  show("signed-in", Boolean(idToken));
  if (idToken) $("who").textContent = decodeJwtPayload(idToken).email || "staff";
}

main();
