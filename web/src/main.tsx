import React from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import "./styles.css";

// A last-resort guard so a render-time throw degrades to a readable message
// instead of a 100% blank page. The store migrates old persisted data on load,
// but if anything else ever throws during render we'd rather say so — and offer a
// clean reset — than show white.
class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="app-crash" role="alert">
        <h1>Something went wrong</h1>
        <p>The workspace hit an unexpected error and couldn’t render.</p>
        <button
          type="button"
          onClick={() => {
            // Stale local data is the most likely cause; clear it and reload.
            try {
              window.localStorage.removeItem("attest.uploads.v1");
            } catch {
              /* storage unavailable — nothing to clear */
            }
            window.location.reload();
          }}
        >
          Reset workspace and reload
        </button>
      </div>
    );
  }
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);
