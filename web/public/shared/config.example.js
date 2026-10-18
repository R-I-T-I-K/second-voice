// Copy to config.js. In local dev the values below work with the emulators.
window.SV_CONFIG = {
  firebase: { apiKey: "demo-key", authDomain: "second-voice-demo.firebaseapp.com", projectId: "second-voice-demo" },
  useEmulators: true,
  emulators: { firestoreHost: "localhost", firestorePort: 8080, authUrl: "http://localhost:9099" },
  bankA: "http://localhost:8081",
  bankB: "http://localhost:8082",
};
