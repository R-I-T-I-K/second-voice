// Shared Firebase bootstrap for the app and both consoles. Firebase JS SDK from
// Google's CDN; Firestore listeners drive every screen, the Cloud Run services
// do every write.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import { getFirestore, connectFirestoreEmulator, collection, doc, onSnapshot, query, orderBy, limit, where }
  from "https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js";
import { getAuth, connectAuthEmulator, signInAnonymously, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";

const cfg = window.SV_CONFIG;
const app = initializeApp(cfg.firebase);
export const db = getFirestore(app);
export const auth = getAuth(app);
if (cfg.useEmulators) {
  connectFirestoreEmulator(db, cfg.emulators.firestoreHost, cfg.emulators.firestorePort);
  connectAuthEmulator(auth, cfg.emulators.authUrl, { disableWarnings: true });
}

export const ready = new Promise((resolve) => {
  onAuthStateChanged(auth, (u) => { if (u) resolve(u); });
  signInAnonymously(auth).catch((e) => console.error("anonymous sign-in failed", e));
});

export const bankUrl = (bank) => (bank === "B" ? cfg.bankB : cfg.bankA);

export async function api(bank, path, body, method = "POST") {
  const r = await fetch(bankUrl(bank) + path, {
    method, headers: { "content-type": "application/json" }, body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${method} ${path} -> ${r.status} ${await r.text()}`);
  return r.json();
}

export const bankCol = (bank, name) => collection(db, "banks", bank, name);
export const caseDoc = (bank, id) => doc(db, "banks", bank, "cases", id);
export const turnsCol = (bank, id) => collection(db, "banks", bank, "cases", id, "turns");
export { collection, doc, onSnapshot, query, orderBy, limit, where };

export const inr = (n) => "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 0 });
export const when = (ts) => (ts?.toDate ? ts.toDate() : new Date(ts)).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
